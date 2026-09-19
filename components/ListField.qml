import QtQuick
import qs.Commons
import qs.Ui

// Repeated rows for a `list` schema field: commands, apps, stop commands.
// Each row is either one scalar input or a group of scalar inputs for an
// object item. The Repeater is driven by the row count, not the array, so
// typing in a row never rebuilds it and never loses focus.
Column {
  id: list

  property var spec: ({})
  property var value: []
  property color foreground: Color.popups.text
  property color muted: Qt.alpha(foreground, 0.6)
  property string fontFamily: Style.font.family

  signal changed(var value)

  readonly property var items: Array.isArray(value) ? value : []
  readonly property var item: spec.item || ({ kind: "string" })
  readonly property bool objectRows: String(item.kind) === "object"
  readonly property var itemFields: item.fields || []
  readonly property color rowBorder: Qt.alpha(foreground, 0.18)

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

  function setKeyAt(index, key, next) {
    var row = items[index] && typeof items[index] === "object" ? clone(items[index]) : emptyRow()
    row[key] = next
    setAt(index, row)
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
            text: String(list.spec.label || "") + " " + (row.index + 1)
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
    text: "Add " + String(list.spec.label || "item").toLowerCase().replace(/s$/, "")
    bordered: true
    foreground: list.foreground
    fontFamily: list.fontFamily
    fontSize: Style.font.caption
    onClicked: list.add()
  }

  Component {
    id: scalarRow

    ScalarField {
      spec: ({ key: "value", kind: String(list.item.kind || "string"), label: "" })
      compact: true
      value: list.items[rowIndex]
      foreground: list.foreground
      muted: list.muted
      fontFamily: list.fontFamily
      onChanged: function(next) { list.setAt(rowIndex, next) }
    }
  }

  Component {
    id: objectRow

    Column {
      id: fieldsColumn
      spacing: Style.spacing.sm
      readonly property int at: rowIndex

      Repeater {
        model: list.itemFields

        ScalarField {
          required property var modelData
          spec: modelData
          compact: true
          value: list.items[fieldsColumn.at] ? list.items[fieldsColumn.at][modelData.key] : null
          foreground: list.foreground
          muted: list.muted
          fontFamily: list.fontFamily
          onChanged: function(next) { list.setKeyAt(fieldsColumn.at, modelData.key, next) }
        }
      }
    }
  }
}
