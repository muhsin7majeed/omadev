import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// omadev: the bar face of a project launcher.
//
// This file only renders. Everything that decides or acts lives in bin/omadev,
// a Python CLI in this directory, and reaches the widget as one JSON object
// per call. The widget owns no timers: status is fetched when the popup opens,
// after a Start or Stop finishes, and when the projects file changes on disk.
// While the popup is closed nothing here runs.
//
// Colors, fonts and spacing come from the shell's Color and Style singletons
// and from the bar that hosts the widget, so every Omarchy theme applies.
//
// Glyphs are written as \u escapes so the source survives editors that mangle
// private-use codepoints.
Panel {
  id: root

  moduleName: "potato.omadev"
  ipcTarget: "potato.omadev"

  readonly property color foreground: bar ? bar.barForeground : Color.foreground
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property color popupText: Color.popups.text
  readonly property color popupMuted: Qt.alpha(Color.popups.text, 0.6)
  readonly property int panelWidth: setting("panelWidth", 380)

  // The CLI ships next to this file. python3 is named explicitly so the
  // executable bit on the script is a convenience, not a requirement.
  readonly property string pluginDir: String(Qt.resolvedUrl(".")).replace(/^file:\/\//, "").replace(/\/$/, "")
  readonly property string cliPath: pluginDir + "/bin/omadev"

  readonly property string configHome: {
    var xdg = Quickshell.env("XDG_CONFIG_HOME")
    return xdg && xdg !== "" ? xdg : Quickshell.env("HOME") + "/.config"
  }
  readonly property string projectsPath: configHome + "/omadev/projects.json"

  // ------------------------------------------------------------------ state
  //
  // phase: "idle" before the first open, then "loading", "error", "empty" or
  // "list". The popup shows exactly one of the last four.
  property string phase: "idle"
  property var projects: []
  property string errorText: ""
  property bool gotStatusOutput: false

  // One action at a time. `busyProject`/`busyAction` name it while it runs;
  // `actionResults` keeps the last step list per project until the popup
  // closes, so what happened stays readable after the spinner is gone.
  property string busyProject: ""
  property string busyAction: ""
  property var actionResults: ({})
  property bool gotActionOutput: false

  function reload() {
    if (statusProc.running) return
    gotStatusOutput = false
    phase = "loading"
    statusProc.running = true
  }

  function fail(message) {
    errorText = message
    phase = "error"
  }

  function parseCli(text) {
    // The CLI prints exactly one JSON object with an `ok` field.
    var data
    try {
      data = JSON.parse(text)
    } catch (e) {
      return { ok: false, error: "The omadev CLI returned something that is not JSON." }
    }
    if (!data || typeof data !== "object") return { ok: false, error: "The omadev CLI returned an empty result." }
    return data
  }

  function applyStatus(text) {
    gotStatusOutput = true
    var data = parseCli(text)
    if (data.ok !== true) {
      fail(data.error ? String(data.error) : "The omadev CLI reported a failure.")
      return
    }
    projects = Array.isArray(data.projects) ? data.projects : []
    phase = projects.length === 0 ? "empty" : "list"
  }

  function runAction(action, name) {
    if (busyProject !== "" || actionProc.running) return
    busyProject = name
    busyAction = action
    gotActionOutput = false
    actionProc.command = ["python3", root.cliPath, action, name, "--json"]
    actionProc.running = true
  }

  function applyAction(text) {
    gotActionOutput = true
    var data = parseCli(text)
    var steps = Array.isArray(data.steps) ? data.steps : []
    if (steps.length === 0) {
      steps = [{ step: busyAction, status: "failed", detail: data.error ? String(data.error) : "no output" }]
    }
    setResults(busyProject, steps)
  }

  function setResults(name, steps) {
    // Reassign the whole object: bindings re-evaluate on assignment, not on
    // in-place mutation.
    var next = {}
    for (var key in actionResults) next[key] = actionResults[key]
    next[name] = steps
    actionResults = next
  }

  function finishAction() {
    busyProject = ""
    busyAction = ""
    reload()
  }

  function resultsFor(name) {
    var list = actionResults[name]
    return Array.isArray(list) ? list : []
  }

  // ---------------------------------------------------------- derived text

  // Is anything of this project up? Ports held by another project do not
  // count: that is exactly the case where "running" would be a lie.
  function isUp(project) {
    if (project.url && project.url.reachable && project.url.owner !== "other") return true
    var commands = project.commands || []
    for (var i = 0; i < commands.length; i++) {
      if (commands[i].listening && commands[i].owner !== "other") return true
    }
    return false
  }

  function heldByOther(project) {
    if (project.url && project.url.owner === "other") return String(project.url.detail || "")
    var commands = project.commands || []
    for (var i = 0; i < commands.length; i++) {
      if (commands[i].owner === "other") return String(commands[i].detail || "")
    }
    return ""
  }

  function summary(project) {
    var parts = []
    if (project.workspace && project.workspace.present) parts.push(String(project.multiplexer || "") + " " + String(project.workspace.id || "")).trim()
    if (project.url) {
      if (project.url.reachable) parts.push("url responding")
      else if (project.url.listening) parts.push("port open, no answer yet")
      else parts.push("url down")
    }
    if (project.editor_open === true) parts.push("editor open")
    var apps = project.apps || []
    var open = 0
    for (var i = 0; i < apps.length; i++) if (apps[i].open) open++
    if (apps.length > 0) parts.push(open + "/" + apps.length + " apps")
    return parts.join("  ·  ")
  }

  function statusColor(status) {
    if (status === "failed") return Color.urgent
    if (status === "started" || status === "stopped" || status === "focused") return Color.accent
    return root.popupMuted
  }

  // ------------------------------------------------------------- processes

  Process {
    id: statusProc
    command: ["python3", root.cliPath, "status", "--json"]
    stdout: StdioCollector {
      onStreamFinished: root.applyStatus(text)
    }
    stderr: StdioCollector {
      id: statusErr
    }
    onExited: function(exitCode, exitStatus) {
      // The CLI prints a JSON object even when it fails, so an empty stdout
      // with a non-zero exit means it never got that far: python3 missing,
      // a traceback, a wrong path. stderr is the only explanation then.
      if (root.gotStatusOutput || exitCode === 0) return
      var detail = String(statusErr.text || "").trim()
      root.fail(detail !== "" ? detail : "The omadev CLI exited with code " + exitCode + " and printed nothing.")
    }
  }

  Process {
    id: actionProc
    stdout: StdioCollector {
      onStreamFinished: root.applyAction(text)
    }
    stderr: StdioCollector {
      id: actionErr
    }
    onExited: function(exitCode, exitStatus) {
      if (!root.gotActionOutput) {
        var detail = String(actionErr.text || "").trim()
        root.setResults(root.busyProject, [{
          step: root.busyAction, status: "failed",
          detail: detail !== "" ? detail : "The omadev CLI exited with code " + exitCode + " and printed nothing."
        }])
      }
      root.finishAction()
    }
  }

  // Watched with inotify, so a projects file edited by hand or by the CLI is
  // reflected without polling. Nothing is read from it here: the CLI is the
  // only reader, so validation happens in one place.
  FileView {
    path: root.projectsPath
    watchChanges: true
    printErrors: false
    onFileChanged: if (root.opened && root.busyProject === "") root.reload()
  }

  onOpenedChanged: {
    if (opened) {
      reload()
    } else {
      actionResults = ({})
    }
  }

  // -------------------------------------------------------------------- bar

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  BarIconButton {
    id: button
    anchors.left: parent.left
    anchors.top: parent.top
    anchors.bottom: parent.bottom
    bar: root.bar
    // U+F0D6E, the glyph Omarchy's own menu uses for "Development".
    text: "󰵮"
    tooltipText: "Projects"
    onPressed: function(b) { root.toggle() }
  }

  // ------------------------------------------------------------------ popup

  KeyboardPanel {
    id: popup
    anchorItem: button
    bar: root.bar
    owner: root
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: popup.fittedContentWidth(Style.space(root.panelWidth))
    contentHeight: popup.fittedContentHeight(content.implicitHeight)

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }

      Column {
        id: content
        anchors.left: parent.left
        anchors.right: parent.right
        spacing: Style.spacing.lg

        Item {
          width: parent.width
          height: Math.max(title.implicitHeight, refresh.height)

          PanelSectionHeader {
            id: title
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            text: "PROJECTS"
            foreground: root.popupText
            fontFamily: root.fontFamily
          }

          PanelActionButton {
            id: refresh
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            // U+F0450, nf-md-refresh.
            iconText: "󰑐"
            tooltipText: "Check again"
            enabled: root.phase !== "loading" && root.busyProject === ""
            foreground: root.popupText
            fontFamily: root.fontFamily
            onClicked: root.reload()
          }
        }

        Text {
          visible: root.phase === "loading" || root.phase === "idle"
          width: parent.width
          textFormat: Text.PlainText
          text: "Checking…"
          color: root.popupMuted
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
        }

        Column {
          visible: root.phase === "error"
          width: parent.width
          spacing: Style.spacing.sm

          Text {
            width: parent.width
            wrapMode: Text.Wrap
            textFormat: Text.PlainText
            text: "omadev could not read your projects."
            color: Color.urgent
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
          }

          Text {
            width: parent.width
            wrapMode: Text.Wrap
            textFormat: Text.PlainText
            text: root.errorText
            color: root.popupText
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
          }
        }

        Column {
          visible: root.phase === "empty"
          width: parent.width
          spacing: Style.spacing.sm

          Text {
            width: parent.width
            wrapMode: Text.Wrap
            textFormat: Text.PlainText
            text: "No projects configured yet."
            color: root.popupText
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
          }

          Text {
            width: parent.width
            wrapMode: Text.Wrap
            textFormat: Text.PlainText
            text: "Projects live in " + root.projectsPath + ". Adding them from this panel comes next."
            color: root.popupMuted
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
          }
        }

        Column {
          visible: root.phase === "list"
          width: parent.width
          spacing: Style.spacing.lg

          Repeater {
            model: root.projects

            Column {
              id: row
              required property var modelData
              required property int index

              readonly property string name: String(modelData.name || "")
              readonly property bool busy: root.busyProject === name
              readonly property bool up: root.isUp(modelData)
              readonly property string held: root.heldByOther(modelData)
              readonly property var steps: root.resultsFor(name)

              width: parent.width
              spacing: Style.spacing.sm

              PanelSeparator {
                visible: row.index > 0
                foreground: root.popupText
              }

              Item {
                width: parent.width
                height: Math.max(labels.implicitHeight, actions.implicitHeight)

                Column {
                  id: labels
                  anchors.left: parent.left
                  anchors.right: actions.left
                  anchors.rightMargin: Style.spacing.lg
                  anchors.verticalCenter: parent.verticalCenter
                  spacing: Style.spacing.xxs

                  Row {
                    spacing: Style.spacing.md

                    Rectangle {
                      anchors.verticalCenter: parent.verticalCenter
                      width: Style.space(7)
                      height: width
                      radius: width / 2
                      color: row.held !== "" ? Color.urgent : (row.up ? Color.accent : root.popupMuted)
                    }

                    Text {
                      textFormat: Text.PlainText
                      text: row.name
                      color: root.popupText
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.subtitle
                    }
                  }

                  Text {
                    width: parent.width
                    elide: Text.ElideRight
                    textFormat: Text.PlainText
                    text: row.busy
                      ? (root.busyAction === "start" ? "Starting…" : "Stopping…")
                      : (row.up ? "running" : "stopped") + (root.summary(modelData) !== "" ? "  ·  " + root.summary(modelData) : "")
                    color: root.popupMuted
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption
                  }

                  Text {
                    visible: row.held !== ""
                    width: parent.width
                    wrapMode: Text.Wrap
                    textFormat: Text.PlainText
                    text: "port held by " + row.held
                    color: Color.urgent
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption
                  }
                }

                Row {
                  id: actions
                  anchors.right: parent.right
                  anchors.verticalCenter: parent.verticalCenter
                  spacing: Style.spacing.sm

                  Button {
                    text: "Start"
                    bordered: true
                    enabled: !row.busy && root.busyProject === ""
                    foreground: root.popupText
                    fontFamily: root.fontFamily
                    fontSize: Style.font.caption
                    onClicked: root.runAction("start", row.name)
                  }

                  Button {
                    text: "Stop"
                    bordered: true
                    enabled: !row.busy && root.busyProject === ""
                    foreground: root.popupText
                    fontFamily: root.fontFamily
                    fontSize: Style.font.caption
                    onClicked: root.runAction("stop", row.name)
                  }
                }
              }

              Column {
                visible: row.steps.length > 0 && !row.busy
                width: parent.width
                spacing: Style.spacing.xxs

                Repeater {
                  model: row.steps

                  Item {
                    required property var modelData
                    width: parent.width
                    height: stepDetail.implicitHeight

                    Text {
                      id: stepStatus
                      anchors.left: parent.left
                      anchors.top: parent.top
                      width: Style.space(58)
                      textFormat: Text.PlainText
                      text: String(modelData.status || "")
                      color: root.statusColor(String(modelData.status || ""))
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.caption
                    }

                    Text {
                      id: stepDetail
                      anchors.left: stepStatus.right
                      anchors.right: parent.right
                      anchors.top: parent.top
                      wrapMode: Text.Wrap
                      textFormat: Text.PlainText
                      text: String(modelData.step || "") + "  " + String(modelData.detail || "")
                      color: String(modelData.status || "") === "failed" ? root.popupText : root.popupMuted
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.caption
                    }
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}
