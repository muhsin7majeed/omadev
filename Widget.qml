import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

import "components"

// omadev: the bar face of a project launcher.
//
// This file only renders. Everything that decides or acts lives in bin/omadev,
// a Python CLI in this directory, and reaches the widget as one JSON object
// per call. The widget owns no timers: status is fetched when the popup opens,
// after a Start or Stop finishes, and when the projects file changes on disk.
// While the popup is closed nothing here runs.
//
// The add/edit form is rendered from `omadev schema --json`, so a new project
// field is added in the CLI and the form follows. The form hands its draft to
// `omadev add` or `omadev edit`, which normalise and validate it exactly as
// they validate the projects file.
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
  // view: "list" or "form".
  // phase: "idle" before the first open, then "loading", "error", "empty" or
  // "list". The list view shows exactly one of the last four.
  property string view: "list"
  property string phase: "idle"
  property var projects: []
  property string errorText: ""
  property bool gotStatusOutput: false

  // One Start/Stop at a time. `busyProject`/`busyAction` name it while it
  // runs; `actionResults` keeps the last step list per project until the
  // popup closes, so what happened stays readable after the spinner is gone.
  property string busyProject: ""
  property string busyAction: ""
  property var actionResults: ({})
  property bool gotActionOutput: false

  // The form. `schemaFields` is fetched once per popup session. `formEditing`
  // is the original name of the project being edited, or "" for a new one.
  // The form owns its draft (assigned, never bound, so its own edits stick).
  property var schemaFields: []
  property var schemaSections: []
  property string formEditing: ""
  property string formError: ""
  property string formErrorKey: ""
  property bool formBusy: false
  property string pendingOpen: ""          // "" | "new" | project name, while schema/show load
  property string confirmRemove: ""         // project name awaiting confirmation

  readonly property bool anyBusy: busyProject !== "" || formBusy || confirmRemove !== ""

  // ------------------------------------------------------------- status

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

  function cliFailure(data, exitCode, stderrText) {
    if (data && data.error) return String(data.error)
    var detail = String(stderrText || "").trim()
    return detail !== "" ? detail : "The omadev CLI exited with code " + exitCode + " and printed nothing."
  }

  function applyStatus(text) {
    gotStatusOutput = true
    var data = parseCli(text)
    if (data.ok !== true) {
      fail(cliFailure(data, 1, ""))
      return
    }
    projects = Array.isArray(data.projects) ? data.projects : []
    phase = projects.length === 0 ? "empty" : "list"
  }

  // ------------------------------------------------------------- actions

  function runAction(action, name) {
    if (anyBusy || actionProc.running) return
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

  // ---------------------------------------------------------------- form

  function emptyDraft() {
    var draft = {}
    for (var i = 0; i < schemaFields.length; i++) {
      var field = schemaFields[i]
      if (field["default"] !== undefined) draft[field.key] = field["default"]
      else if (field.kind === "list") draft[field.key] = []
      else if (field.kind === "object") draft[field.key] = null
      else draft[field.key] = ""
    }
    return draft
  }

  // Open the form for a new project ("new") or to edit `name`. The schema is
  // fetched on first use; the project's full configuration comes from
  // `omadev show`, since the status list carries only a summary.
  function openForm(target) {
    if (anyBusy || pendingOpen !== "") return
    pendingOpen = target
    formError = ""
    formErrorKey = ""
    if (schemaFields.length === 0) {
      schemaProc.running = true
      return
    }
    continueOpen()
  }

  function continueOpen() {
    if (pendingOpen === "") return
    if (pendingOpen === "new") {
      formEditing = ""
      form.draft = emptyDraft()
      pendingOpen = ""
      view = "form"
      return
    }
    showProc.command = ["python3", root.cliPath, "show", pendingOpen, "--json"]
    showProc.running = true
  }

  function applySchema(text) {
    var data = parseCli(text)
    if (data.ok !== true || !Array.isArray(data.fields)) {
      pendingOpen = ""
      fail(cliFailure(data, 1, ""))
      return
    }
    schemaFields = data.fields
    schemaSections = Array.isArray(data.sections) ? data.sections : []
    continueOpen()
  }

  function applyShow(text) {
    var data = parseCli(text)
    var name = pendingOpen
    pendingOpen = ""
    if (data.ok !== true || !data.project) {
      fail(cliFailure(data, 1, ""))
      return
    }
    var draft = emptyDraft()
    for (var key in data.project) draft[key] = data.project[key]
    formEditing = name
    form.draft = draft
    view = "form"
  }

  function closeForm() {
    view = "list"
    formBusy = false
    formError = ""
    formErrorKey = ""
    pendingOpen = ""
  }

  function saveForm(draft) {
    if (formBusy || saveProc.running) return
    formBusy = true
    formError = ""
    formErrorKey = ""
    var payload = JSON.stringify(draft)
    saveProc.command = formEditing === ""
      ? ["python3", root.cliPath, "add", payload, "--json"]
      : ["python3", root.cliPath, "edit", formEditing, payload, "--json"]
    saveProc.running = true
  }

  // "project 'demo'.commands[0].port" -> "commands", so the form can mark
  // the field the CLI complained about.
  function fieldKeyFromWhere(where) {
    var text = String(where || "").replace(/^project(\s+'[^']*')?\.?/, "")
    var match = text.match(/^[A-Za-z_]+/)
    return match ? match[0] : ""
  }

  function applySave(text) {
    formBusy = false
    var data = parseCli(text)
    if (data.ok !== true) {
      formError = cliFailure(data, 1, "")
      formErrorKey = fieldKeyFromWhere(data.where)
      return
    }
    closeForm()
    reload()
  }

  function removeProject(name) {
    if (removeProc.running) return
    removeProc.command = ["python3", root.cliPath, "remove", name, "--json"]
    removeProc.running = true
  }

  function applyRemove(text) {
    var data = parseCli(text)
    confirmRemove = ""
    if (data.ok !== true) {
      if (view === "form") formError = cliFailure(data, 1, "")
      else fail(cliFailure(data, 1, ""))
      return
    }
    if (view === "form") closeForm()
    reload()
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
    if (project.multiplexer !== "none" && project.workspace && project.workspace.present) parts.push((String(project.multiplexer || "") + " " + String(project.workspace.id || "")).trim())
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
      root.fail(root.cliFailure(null, exitCode, statusErr.text))
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
        root.setResults(root.busyProject, [{ step: root.busyAction, status: "failed", detail: root.cliFailure(null, exitCode, actionErr.text) }])
      }
      root.finishAction()
    }
  }

  Process {
    id: schemaProc
    command: ["python3", root.cliPath, "schema", "--json"]
    stdout: StdioCollector {
      onStreamFinished: root.applySchema(text)
    }
  }

  Process {
    id: showProc
    stdout: StdioCollector {
      onStreamFinished: root.applyShow(text)
    }
  }

  Process {
    id: saveProc
    stdout: StdioCollector {
      onStreamFinished: root.applySave(text)
    }
    stderr: StdioCollector {
      id: saveErr
    }
    onExited: function(exitCode, exitStatus) {
      if (root.formBusy && exitCode !== 0) {
        // No JSON came back: the CLI itself crashed.
        root.formBusy = false
        root.formError = root.cliFailure(null, exitCode, saveErr.text)
      }
    }
  }

  Process {
    id: removeProc
    stdout: StdioCollector {
      onStreamFinished: root.applyRemove(text)
    }
  }

  // Watched with inotify, so a projects file edited by hand or by the CLI is
  // reflected without polling. Nothing is read from it here: the CLI is the
  // only reader, so validation happens in one place.
  FileView {
    path: root.projectsPath
    watchChanges: true
    printErrors: false
    onFileChanged: if (root.opened && root.busyProject === "" && root.view === "list") root.reload()
  }

  // One line per load, so `quickshell log -p $OMARCHY_PATH/shell` shows
  // whether a hot reload actually picked the widget up.
  Component.onCompleted: console.log("potato.omadev: widget loaded")

  onOpenedChanged: {
    if (opened) {
      reload()
    } else {
      actionResults = ({})
      confirmRemove = ""
      closeForm()
    }
  }

  // Development hooks: open the form from the terminal so it can be checked
  // without clicking. `omarchy-shell potato.omadev.dev newProject`.
  IpcHandler {
    target: "potato.omadev.dev"

    function newProject(): void { root.open(); root.openForm("new") }
    function editProject(name: string): void { root.open(); root.openForm(name) }
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
    // U+F14DE, nf-md-rocket-launch.
    text: "󱓞"
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
    // The form has nested rows and needs room; the list does not.
    contentWidth: popup.fittedContentWidth(Style.space(root.view === "form" ? Math.max(root.panelWidth, 480) : root.panelWidth))
    contentHeight: popup.fittedContentHeight(content.implicitHeight)

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      // Text fields own the keyboard while the form is open; the dialog
      // handles its own keys.
      blocked: root.view === "form" || root.confirmRemove !== ""
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }

      Column {
        id: content
        anchors.left: parent.left
        anchors.right: parent.right
        spacing: Style.spacing.lg

        // ----------------------------------------------------------- form

        ProjectForm {
          id: form
          visible: root.view === "form"
          width: parent.width
          sections: root.schemaSections
          title: root.formEditing === "" ? "New project" : "Edit " + root.formEditing
          editing: root.formEditing !== ""
          busy: root.formBusy
          errorText: root.formError
          errorKey: root.formErrorKey
          maxFieldsHeight: Math.max(Style.space(200), popup.availableCardHeight - Style.space(200))
          foreground: root.popupText
          muted: root.popupMuted
          fontFamily: root.fontFamily
          onSaveRequested: function(project) { root.saveForm(project) }
          onCancelRequested: root.closeForm()
          onDeleteRequested: root.confirmRemove = root.formEditing
        }

        // ----------------------------------------------------------- list

        Item {
          visible: root.view === "list"
          width: parent.width
          height: Math.max(title.implicitHeight, headerActions.height)

          PanelSectionHeader {
            id: title
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            text: "PROJECTS"
            foreground: root.popupText
            fontFamily: root.fontFamily
          }

          Row {
            id: headerActions
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            spacing: Style.spacing.xs

            PanelActionButton {
              anchors.verticalCenter: parent.verticalCenter
              // U+F0415, nf-md-plus.
              iconText: "󰐕"
              tooltipText: "Add a project"
              enabled: !root.anyBusy && root.pendingOpen === ""
              foreground: root.popupText
              fontFamily: root.fontFamily
              onClicked: root.openForm("new")
            }

            PanelActionButton {
              anchors.verticalCenter: parent.verticalCenter
              // U+F0450, nf-md-refresh.
              iconText: "󰑐"
              tooltipText: "Check again"
              enabled: root.phase !== "loading" && !root.anyBusy
              foreground: root.popupText
              fontFamily: root.fontFamily
              onClicked: root.reload()
            }
          }
        }

        Text {
          visible: root.view === "list" && (root.phase === "loading" || root.phase === "idle")
          width: parent.width
          textFormat: Text.PlainText
          text: "Checking…"
          color: root.popupMuted
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
        }

        Column {
          visible: root.view === "list" && root.phase === "error"
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
          visible: root.view === "list" && root.phase === "empty"
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
            text: "Use + above to add one. Projects are stored in " + root.projectsPath + "."
            color: root.popupMuted
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
          }
        }

        Column {
          visible: root.view === "list" && root.phase === "list"
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
                    anchors.verticalCenter: parent.verticalCenter
                    text: "Start"
                    bordered: true
                    enabled: !root.anyBusy
                    foreground: root.popupText
                    fontFamily: root.fontFamily
                    fontSize: Style.font.caption
                    onClicked: root.runAction("start", row.name)
                  }

                  Button {
                    anchors.verticalCenter: parent.verticalCenter
                    text: "Stop"
                    bordered: true
                    enabled: !root.anyBusy
                    foreground: root.popupText
                    fontFamily: root.fontFamily
                    fontSize: Style.font.caption
                    onClicked: root.runAction("stop", row.name)
                  }

                  PanelActionButton {
                    anchors.verticalCenter: parent.verticalCenter
                    // U+F03EB, nf-md-pencil.
                    iconText: "󰏫"
                    tooltipText: "Edit " + row.name
                    enabled: !root.anyBusy && root.pendingOpen === ""
                    foreground: root.popupMuted
                    hoverColor: root.popupText
                    fontFamily: root.fontFamily
                    onClicked: root.openForm(row.name)
                  }

                  PanelActionButton {
                    anchors.verticalCenter: parent.verticalCenter
                    // U+F01B4, nf-md-delete.
                    iconText: "󰆴"
                    tooltipText: "Remove " + row.name + " from the list"
                    enabled: !root.anyBusy
                    foreground: root.popupMuted
                    hoverColor: Color.urgent
                    fontFamily: root.fontFamily
                    onClicked: root.confirmRemove = row.name
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

      // Removing only forgets the project; nothing running is touched, and
      // the dialog says so.
      ConfirmDialog {
        anchors.fill: parent
        z: 10
        opened: root.confirmRemove !== ""
        message: "Remove '" + root.confirmRemove + "' from omadev? Nothing running is stopped; only the configuration is deleted."
        confirmText: "Remove"
        background: Color.popups.background
        foreground: root.popupText
        fontFamily: root.fontFamily
        onCanceled: root.confirmRemove = ""
        onConfirmed: root.removeProject(root.confirmRemove)
      }
    }
  }
}
