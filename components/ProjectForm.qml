import QtQuick
import qs.Commons
import qs.Ui

// The add/edit form, rendered from the schema the CLI describes. It holds a
// draft object and hands it back untouched on Save; the CLI normalises and
// validates, and any error comes back here with the field it belongs to.
Column {
  id: form

  property var schema: []
  property var draft: ({})
  property string title: "New project"
  property bool editing: false
  property bool busy: false
  property string errorText: ""
  property string errorKey: ""
  property real maxFieldsHeight: Style.space(480)
  property color foreground: Color.popups.text
  property color muted: Qt.alpha(foreground, 0.6)
  property string fontFamily: Style.font.family

  signal saveRequested(var project)
  signal cancelRequested()
  signal deleteRequested()

  // Reassign the whole draft: bindings re-evaluate on assignment, not on
  // in-place mutation.
  function set(key, next) {
    var copy = {}
    for (var k in draft) copy[k] = draft[k]
    copy[key] = next
    draft = copy
    if (errorKey === key) {
      errorKey = ""
      errorText = ""
    }
  }

  width: parent ? parent.width : implicitWidth
  spacing: Style.spacing.lg

  Item {
    width: parent.width
    height: Math.max(back.height, heading.implicitHeight)

    PanelActionButton {
      id: back
      anchors.left: parent.left
      anchors.verticalCenter: parent.verticalCenter
      // U+F004D, nf-md-arrow-left.
      iconText: "󰁍"
      tooltipText: "Back without saving"
      foreground: form.foreground
      fontFamily: form.fontFamily
      enabled: !form.busy
      onClicked: form.cancelRequested()
    }

    PanelSectionHeader {
      id: heading
      anchors.left: back.right
      anchors.leftMargin: Style.spacing.sm
      anchors.verticalCenter: parent.verticalCenter
      text: form.title.toUpperCase()
      foreground: form.foreground
      fontFamily: form.fontFamily
    }
  }

  Text {
    visible: form.errorText !== ""
    width: parent.width
    wrapMode: Text.Wrap
    textFormat: Text.PlainText
    text: form.errorText
    color: Color.urgent
    font.family: form.fontFamily
    font.pixelSize: Style.font.caption
  }

  Flickable {
    id: scroller
    width: parent.width
    height: Math.min(fields.implicitHeight, form.maxFieldsHeight)
    contentWidth: width
    contentHeight: fields.implicitHeight
    clip: true
    boundsBehavior: Flickable.StopAtBounds
    interactive: contentHeight > height

    Column {
      id: fields
      width: scroller.width
      spacing: Style.spacing.xxl

      Repeater {
        model: form.schema

        Loader {
          id: slot
          required property var modelData
          readonly property string key: String(modelData.key || "")
          readonly property string kind: String(modelData.kind || "string")

          width: parent.width
          sourceComponent: kind === "list" ? listField : (kind === "object" ? objectField : scalarField)

          onLoaded: {
            item.spec = modelData
            item.value = Qt.binding(function() { return form.draft[slot.key] })
            item.changed.connect(function(next) { form.set(slot.key, next) })
            if ("invalid" in item) item.invalid = Qt.binding(function() { return form.errorKey === slot.key })
          }
        }
      }
    }
  }

  Component {
    id: scalarField

    ScalarField {
      foreground: form.foreground
      muted: form.muted
      fontFamily: form.fontFamily
    }
  }

  // Object and list fields draw their own label so the group reads as one.
  Component {
    id: objectField

    Column {
      property var spec: ({})
      property var value: null
      property bool invalid: false
      signal changed(var value)

      width: parent ? parent.width : implicitWidth
      spacing: Style.spacing.xs

      Text {
        textFormat: Text.PlainText
        text: String(parent.spec.label || "")
        color: parent.invalid ? Color.urgent : form.foreground
        font.family: form.fontFamily
        font.pixelSize: Style.font.bodySmall
        font.bold: true
      }

      Text {
        visible: text !== ""
        width: parent.width
        wrapMode: Text.Wrap
        textFormat: Text.PlainText
        text: String(parent.spec.help || "")
        color: form.muted
        font.family: form.fontFamily
        font.pixelSize: Style.font.caption
      }

      ObjectFields {
        spec: parent.spec
        value: parent.value
        foreground: form.foreground
        muted: form.muted
        fontFamily: form.fontFamily
        onChanged: function(next) { parent.changed(next) }
      }
    }
  }

  Component {
    id: listField

    Column {
      property var spec: ({})
      property var value: []
      property bool invalid: false
      signal changed(var value)

      width: parent ? parent.width : implicitWidth
      spacing: Style.spacing.xs

      Text {
        textFormat: Text.PlainText
        text: String(parent.spec.label || "")
        color: parent.invalid ? Color.urgent : form.foreground
        font.family: form.fontFamily
        font.pixelSize: Style.font.bodySmall
        font.bold: true
      }

      Text {
        visible: text !== ""
        width: parent.width
        wrapMode: Text.Wrap
        textFormat: Text.PlainText
        text: String(parent.spec.help || "")
        color: form.muted
        font.family: form.fontFamily
        font.pixelSize: Style.font.caption
      }

      ListField {
        spec: parent.spec
        value: parent.value
        foreground: form.foreground
        muted: form.muted
        fontFamily: form.fontFamily
        onChanged: function(next) { parent.changed(next) }
      }
    }
  }

  Row {
    spacing: Style.spacing.sm

    Button {
      text: form.busy ? "Saving…" : "Save"
      bordered: true
      active: true
      enabled: !form.busy
      foreground: form.foreground
      fontFamily: form.fontFamily
      fontSize: Style.font.caption
      onClicked: form.saveRequested(form.draft)
    }

    Button {
      text: "Cancel"
      bordered: true
      enabled: !form.busy
      foreground: form.foreground
      fontFamily: form.fontFamily
      fontSize: Style.font.caption
      onClicked: form.cancelRequested()
    }

    Button {
      visible: form.editing
      text: "Delete project"
      bordered: true
      enabled: !form.busy
      foreground: Color.urgent
      fontFamily: form.fontFamily
      fontSize: Style.font.caption
      onClicked: form.deleteRequested()
    }
  }
}
