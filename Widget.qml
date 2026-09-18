import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// omadev: the bar face of a project launcher.
//
// This file only renders. Everything that decides or acts lives in bin/omadev,
// a Python CLI in this directory, and reaches the widget as one JSON object
// per call. The widget owns no timers: the project list is fetched when the
// popup opens, when the projects file changes on disk, and after an action
// finishes. While the popup is closed nothing here runs.
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
  property bool gotOutput: false

  function reload() {
    if (listProc.running) return
    gotOutput = false
    phase = "loading"
    listProc.running = true
  }

  function fail(message) {
    errorText = message
    phase = "error"
  }

  function applyResult(text) {
    gotOutput = true
    var data
    try {
      data = JSON.parse(text)
    } catch (e) {
      fail("The omadev CLI returned something that is not JSON.")
      return
    }
    if (!data || data.ok !== true) {
      fail(data && data.error ? String(data.error) : "The omadev CLI reported a failure.")
      return
    }
    projects = Array.isArray(data.projects) ? data.projects : []
    phase = projects.length === 0 ? "empty" : "list"
  }

  Process {
    id: listProc
    command: ["python3", root.cliPath, "list", "--json"]
    stdout: StdioCollector {
      onStreamFinished: root.applyResult(text)
    }
    stderr: StdioCollector {
      id: listErr
    }
    onExited: function(exitCode, exitStatus) {
      // The CLI prints a JSON object even when it fails, so an empty stdout
      // with a non-zero exit means it never got that far: python3 missing,
      // a traceback, a wrong path. stderr is the only explanation then.
      if (root.gotOutput || exitCode === 0) return
      var detail = String(listErr.text || "").trim()
      root.fail(detail !== "" ? detail : "The omadev CLI exited with code " + exitCode + " and printed nothing.")
    }
  }

  // Watched with inotify, so a projects file edited by hand or by the CLI is
  // reflected without polling. Nothing is read from it here: the CLI is the
  // only reader, so validation happens in one place.
  FileView {
    path: root.projectsPath
    watchChanges: true
    printErrors: false
    onFileChanged: if (root.opened) root.reload()
  }

  onOpenedChanged: if (opened) reload()

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

        PanelSectionHeader {
          text: "PROJECTS"
          foreground: root.popupText
          fontFamily: root.fontFamily
        }

        Text {
          visible: root.phase === "loading" || root.phase === "idle"
          width: parent.width
          textFormat: Text.PlainText
          text: "Loading…"
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
          spacing: Style.spacing.md

          Repeater {
            model: root.projects

            Column {
              required property var modelData
              width: parent.width
              spacing: Style.spacing.xxs

              Text {
                width: parent.width
                elide: Text.ElideRight
                textFormat: Text.PlainText
                text: String(modelData.name || "")
                color: root.popupText
                font.family: root.fontFamily
                font.pixelSize: Style.font.subtitle
              }

              Text {
                width: parent.width
                elide: Text.ElideMiddle
                textFormat: Text.PlainText
                text: String(modelData.multiplexer || "") + "  ·  " + String(modelData.path || "")
                color: root.popupMuted
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
