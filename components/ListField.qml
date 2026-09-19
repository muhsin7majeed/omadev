import QtQuick
import qs.Commons
import qs.Ui

// Repeated rows for a `list` schema field: processes, setup commands, apps,
// stop commands. Each row is either one scalar input or an ObjectFields
// group for an object item, which brings presets and "More options" along.
// The Repeater is driven by the row count, not the array, so typing in a
// row never rebuilds it and never loses focus.
Column {
  id: list

  property var spec: ({})
  property var value: []
  property bool showHelp: false
  property color foreground: Color.popups.text
  property color muted: Qt.alpha(foreground, 0.6)
  property string fontFamily: Style.font.family

  signal changed(var value)

  readonly property var items: Array.isArray(value) ? value : []
  readonly property var item: spec.item || ({ kind: "string" })
  readonly property bool objectRows: String(item.kind) === "object"
  readonly property var itemFields: item.fields || []
  readonly property color rowBorder: Qt.alpha(foreground, 0.18)
  readonly property string singular: String(spec.label || "item").toLowerCase().replace(/es$/, "").replace(/s$/, "")

  function clone(source) {
    return JSON.parse(JSON.stringify(source))
  }

  function emptyRow() {
    if (!objectRows) return ""
    var out = {}
    for (var i = 0; i < itemFields.length; i++) out[itemFields[i].key] = ""
    return out
  }

  function setAt(index, next) {
    var copy = clone(items)
    copy[index] = next
    changed(copy)
  }

  function removeAt(index) {
    var copy = clone(items)
    copy.splice(index, 1)
    changed(copy)
  }

  function add() {
    changed(clone(items).concat([emptyRow()]))
  }

  width: parent ? parent.width : implicitWidth
  spacing: Style.spacing.sm

  Repeater {
    model: list.items.length

    Rectangle {
      id: row
      required property int index

      width: parent.width
      implicitHeight: rowContent.implicitHeight + Style.spacing.lg * 2
      color: "transparent"
      radius: Style.cornerRadius
      border.width: 1
      border.color: list.rowBorder

      Column {
        id: rowContent
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: Style.spacing.lg
        spacing: Style.spacing.sm

        Item {
          width: parent.width
          height: remove.height

          Text {
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            textFormat: Text.PlainText
            text: list.singular.charAt(0).toUpperCase() + list.singular.slice(1) + " " + (row.index + 1)
            color: list.muted
            font.family: list.fontFamily
            font.pixelSize: Style.font.caption
          }

          PanelActionButton {
            id: remove
            anchors.right: parent.right
            // U+F0156, nf-md-close.
            iconText: "󰅖"
            tooltipText: "Remove"
            foreground: list.muted
            hoverColor: Color.urgent
            fontFamily: list.fontFamily
            onClicked: list.removeAt(row.index)
          }
        }

        Loader {
          width: parent.width
          sourceComponent: list.objectRows ? objectRow : scalarRow

          property int rowIndex: row.index
        }
      }
    }
  }

  Button {
    text: "Add " + list.singular
    bordered: true
    foreground: list.foreground
    fontFamily: list.fontFamily
    fontSize: Style.font.caption
    onClicked: list.add()
  }

  Component {
    id: scalarRow

    ScalarField {
      // A scalar row inherits the list's example; its label and help are
      // already shown once above the rows.
      spec: ({ key: "value", kind: String(list.item.kind || "string"), label: "", example: list.spec.example || "" })
      compact: true
      showHelp: list.showHelp
      value: list.items[rowIndex]
      foreground: list.foreground
      muted: list.muted
      fontFamily: list.fontFamily
      onChanged: function(next) { list.setAt(rowIndex, next) }
    }
  }

  Component {
    id: objectRow

    ObjectFields {
      spec: list.item
      value: list.items[rowIndex]
      showHelp: list.showHelp
      foreground: list.foreground
      muted: list.muted
      fontFamily: list.fontFamily
      onChanged: function(next) { list.setAt(rowIndex, next) }
    }
  }
}
