import QtQuick
import qs.Commons
import qs.Ui

// One labelled input for a scalar field of the project schema: string,
// path, url, regex, argv, integer or enum. It never interprets the value:
// text stays text and the CLI converts and validates. An argv value that
// arrives as a list (from `omadev show`) is displayed as one shell-quoted
// line, which is also what the CLI accepts back.
Column {
  id: field

  property var spec: ({})
  property var value: null
  property bool compact: false
  property bool invalid: false
  // Help is a "?" tooltip by default; the form's toggle shows it inline.
  property bool showHelp: false
  property color foreground: Color.popups.text
  property color muted: Qt.alpha(foreground, 0.6)
  property string fontFamily: Style.font.family

  signal changed(var value)

  readonly property string kind: String(spec.kind || "string")
  readonly property string label: String(spec.label || spec.key || "")
  readonly property string help: String(spec.help || "")
  readonly property string example: String(spec.example || "")
  readonly property bool optional: spec.optional === true
  readonly property string display: kind === "argv" ? argvText(value) : (value === null || value === undefined ? "" : String(value))

  function argvText(list) {
    if (!Array.isArray(list)) return list === null || list === undefined ? "" : String(list)
    return list.map(quote).join(" ")
  }

  function quote(part) {
    var text = String(part)
    if (text === "") return "''"
    return /[\s'"\\$]/.test(text) ? "'" + text.replace(/'/g, "'\\''") + "'" : text
  }

  width: parent ? parent.width : implicitWidth
  spacing: Style.spacing.xs

  Row {
    spacing: Style.spacing.sm

    Text {
      textFormat: Text.PlainText
      text: field.label
      color: field.invalid ? Color.urgent : (field.compact ? field.muted : field.foreground)
      font.family: field.fontFamily
      font.pixelSize: field.compact ? Style.font.caption : Style.font.bodySmall
      font.bold: !field.compact
    }

    Text {
      visible: field.optional
      anchors.verticalCenter: parent.verticalCenter
      textFormat: Text.PlainText
      text: "optional"
      color: field.muted
      font.family: field.fontFamily
      font.pixelSize: Style.font.caption
    }

    HelpHint {
      visible: field.help !== "" && !field.showHelp
      anchors.verticalCenter: parent.verticalCenter
      text: field.help
      foreground: field.foreground
      fontFamily: field.fontFamily
    }
  }

  Loader {
    width: parent.width
    sourceComponent: field.kind === "enum" ? enumInput : textInput
  }

  Text {
    visible: field.showHelp && field.help !== ""
    width: parent.width
    wrapMode: Text.Wrap
    textFormat: Text.PlainText
    text: field.help
    color: field.muted
    font.family: field.fontFamily
    font.pixelSize: Style.font.caption
  }

  Component {
    id: textInput

    TextField {
      width: field.width
      text: field.display
      // The example sits in the empty field as a hint of the expected shape.
      placeholderText: field.example !== "" ? "e.g. " + field.example : ""
      font.family: field.fontFamily
      font.pixelSize: Style.font.body
      foreground: field.foreground
      inputMethodHints: field.kind === "integer" ? Qt.ImhDigitsOnly : Qt.ImhNone
      onTextEdited: field.changed(text)
    }
  }

  Component {
    id: enumInput

    Dropdown {
      width: field.width
      showLabel: false
      options: field.spec.options || []
      value: field.value === null || field.value === undefined ? "" : String(field.value)
      foreground: field.foreground
      fontFamily: field.fontFamily
      onChanged: function(next) { field.changed(next) }
    }
  }
}
