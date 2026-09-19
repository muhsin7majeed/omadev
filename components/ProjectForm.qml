import QtQuick
import qs.Commons
import qs.Ui

// The add/edit form, rendered from the sections the CLI describes. It holds
// a draft object and hands it back untouched on Save; the CLI normalises and
// validates, and any error comes back here with the field it belongs to.
//
// A section field may use a dotted key (`workspaces.terminal`) to reach into
// a stored object, so a value can be shown next to what it belongs to
// without changing where it is stored.
Column {
  id: form

  property var sections: []
  property var draft: ({})
  property string title: "New project"
  property bool editing: false
  property bool busy: false
  property string errorText: ""
  property string errorKey: ""
  // Off: each field has a "?" tooltip. On: every description is inline.
  property bool showHelp: false
  property real maxFieldsHeight: Style.space(480)
  property color foreground: Color.popups.text
  property color muted: Qt.alpha(foreground, 0.6)
  property string fontFamily: Style.font.family

  // Which collapsible sections are open, by section key.
  property var opened: ({})

  signal saveRequested(var project)
  signal cancelRequested()
  signal deleteRequested()

  function get(key) {
    var parts = String(key).split(".")
    var node = draft
    for (var i = 0; i < parts.length; i++) {
      if (node === null || node === undefined || typeof node !== "object") return undefined
      node = node[parts[i]]
    }
    return node
  }

  // Reassign the whole draft: bindings re-evaluate on assignment, not on
  // in-place mutation. A dotted key creates the intermediate object.
  function set(key, next) {
    var parts = String(key).split(".")
    var copy = shallow(draft)
    var node = copy
    for (var i = 0; i < parts.length - 1; i++) {
      var child = node[parts[i]]
      node[parts[i]] = child && typeof child === "object" ? shallow(child) : {}
      node = node[parts[i]]
    }
    node[parts[parts.length - 1]] = next
    draft = copy
    if (errorKey === parts[0]) {
      errorKey = ""
      errorText = ""
    }
  }

  function shallow(source) {
    var out = {}
    for (var k in source) out[k] = source[k]
    return out
  }

  // `visible_if` on a schema field: {key} shows it once that field has a
  // value, {key, not} once that field's value differs from `not`. Purely
  // presentational; the CLI validates the same either way.
  function visibleFor(spec) {
    var cond = spec.visible_if
    if (!cond || !cond.key) return true
    var value = get(cond.key)
    if (cond["not"] !== undefined) return String(value === undefined || value === null ? "" : value) !== String(cond["not"])
    return value !== undefined && value !== null && value !== ""
  }

  // Called by the host when the form is (re)opened: top of the form,
  // collapsible sections closed again, whatever the previous visit left.
  function reset() {
    opened = ({})
    scroller.contentY = 0
  }

  function isOpen(section) {
    if (section.collapsed !== true) return true
    return opened[section.key] === true
  }

  function toggleSection(section) {
    var next = shallow(opened)
    next[section.key] = !isOpen(section)
    opened = next
  }

  function errorIn(section) {
    if (errorKey === "") return false
    for (var i = 0; i < section.fields.length; i++) {
      if (String(section.fields[i].key).split(".")[0] === errorKey) return true
    }
    return false
  }

  width: parent ? parent.width : implicitWidth
  spacing: Style.spacing.lg

  // ------------------------------------------------------------- header

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

    Row {
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      spacing: Style.spacing.xs

      PanelActionButton {
        anchors.verticalCenter: parent.verticalCenter
        // U+F0625, nf-md-help-circle-outline.
        iconText: "󰘥"
        tooltipText: form.showHelp ? "Hide field descriptions" : "Show all field descriptions"
        foreground: form.showHelp ? Color.accent : form.muted
        hoverColor: Color.accent
        fontFamily: form.fontFamily
        onClicked: form.showHelp = !form.showHelp
      }

      // Delete lives up here, far from Save, and still asks first.
      PanelActionButton {
        visible: form.editing
        anchors.verticalCenter: parent.verticalCenter
        // U+F01B4, nf-md-delete.
        iconText: "󰆴"
        tooltipText: "Remove this project from omadev"
        foreground: form.muted
        hoverColor: Color.urgent
        fontFamily: form.fontFamily
        enabled: !form.busy
        onClicked: form.deleteRequested()
      }
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

  // ----------------------------------------------------------- sections

  Flickable {
    id: scroller
    width: parent.width
    height: Math.min(sectionsColumn.implicitHeight, form.maxFieldsHeight)
    contentWidth: width
    contentHeight: sectionsColumn.implicitHeight
    clip: true
    boundsBehavior: Flickable.StopAtBounds
    interactive: contentHeight > height

    Column {
      id: sectionsColumn
      width: scroller.width
      spacing: Style.spacing.xl

      Repeater {
        model: form.sections

        Column {
          id: sectionItem
          required property var modelData
          required property int index
          readonly property bool open: form.isOpen(modelData)
          readonly property bool collapsible: modelData.collapsed === true

          width: parent.width
          spacing: Style.spacing.md

          PanelSeparator {
            visible: sectionItem.index > 0
            foreground: form.foreground
          }

          Item {
            width: parent.width
            height: sectionTitle.implicitHeight + Style.spacing.xs

            Row {
              anchors.left: parent.left
              anchors.verticalCenter: parent.verticalCenter
              spacing: Style.spacing.sm

              Text {
                id: sectionTitle
                textFormat: Text.PlainText
                text: String(sectionItem.modelData.title || "")
                color: form.errorIn(sectionItem.modelData) ? Color.urgent : form.foreground
                font.family: form.fontFamily
                font.pixelSize: Style.font.subtitle
                font.bold: true
              }

              Text {
                visible: sectionItem.collapsible
                anchors.verticalCenter: parent.verticalCenter
                textFormat: Text.PlainText
                text: sectionItem.open ? "hide" : "show"
                color: titleHover.hovered ? Color.accent : form.muted
                font.family: form.fontFamily
                font.pixelSize: Style.font.caption
                font.underline: titleHover.hovered
              }
            }

            HoverHandler { id: titleHover; enabled: sectionItem.collapsible; cursorShape: Qt.PointingHandCursor }
            TapHandler { enabled: sectionItem.collapsible; onTapped: form.toggleSection(sectionItem.modelData) }
          }

          Column {
            visible: sectionItem.open
            width: parent.width
            spacing: Style.spacing.xl

            Repeater {
              model: sectionItem.modelData.fields || []

              Loader {
                id: slot
                required property var modelData
                readonly property string key: String(modelData.key || "")
                readonly property string kind: String(modelData.kind || "string")

                width: parent.width
                visible: form.visibleFor(modelData)
                sourceComponent: kind === "list" ? listField : (kind === "object" ? objectField : scalarField)

                onLoaded: {
                  item.spec = modelData
                  item.value = Qt.binding(function() { return form.get(slot.key) })
                  item.changed.connect(function(next) { form.set(slot.key, next) })
                  if ("invalid" in item) item.invalid = Qt.binding(function() { return form.errorKey === slot.key.split(".")[0] })
                  if ("showHelp" in item) item.showHelp = Qt.binding(function() { return form.showHelp })
                }
              }
            }
          }
        }
      }
    }
  }

  // ------------------------------------------------------------- kinds

  Component {
    id: scalarField

    ScalarField {
      foreground: form.foreground
      muted: form.muted
      fontFamily: form.fontFamily
    }
  }

  Component {
    id: objectField

    Column {
      id: objectGroup
      property var spec: ({})
      property var value: null
      property bool invalid: false
      property bool showHelp: false
      signal changed(var value)

      width: parent ? parent.width : implicitWidth
      spacing: Style.spacing.xs

      GroupLabel {
        spec: objectGroup.spec
        invalid: objectGroup.invalid
        showHelp: objectGroup.showHelp
      }

      ObjectFields {
        spec: objectGroup.spec
        value: objectGroup.value
        showHelp: objectGroup.showHelp
        foreground: form.foreground
        muted: form.muted
        fontFamily: form.fontFamily
        onChanged: function(next) { objectGroup.changed(next) }
      }
    }
  }

  Component {
    id: listField

    Column {
      id: listGroup
      property var spec: ({})
      property var value: []
      property bool invalid: false
      property bool showHelp: false
      signal changed(var value)

      width: parent ? parent.width : implicitWidth
      spacing: Style.spacing.xs

      GroupLabel {
        spec: listGroup.spec
        invalid: listGroup.invalid
        showHelp: listGroup.showHelp
      }

      ListField {
        spec: listGroup.spec
        value: listGroup.value
        showHelp: listGroup.showHelp
        foreground: form.foreground
        muted: form.muted
        fontFamily: form.fontFamily
        onChanged: function(next) { listGroup.changed(next) }
      }
    }
  }

  // The heading of an object or list group: label, "?" hint, and the
  // description inline when the form's toggle is on.
  component GroupLabel: Column {
    property var spec: ({})
    property bool invalid: false
    property bool showHelp: false

    width: parent ? parent.width : implicitWidth
    spacing: Style.spacing.xs

    Row {
      spacing: Style.spacing.sm

      Text {
        textFormat: Text.PlainText
        text: String(parent.parent.spec.label || "")
        color: parent.parent.invalid ? Color.urgent : form.foreground
        font.family: form.fontFamily
        font.pixelSize: Style.font.bodySmall
        font.bold: true
      }

      HelpHint {
        visible: text !== "" && !parent.parent.showHelp
        anchors.verticalCenter: parent.verticalCenter
        text: String(parent.parent.spec.help || "")
        foreground: form.foreground
        fontFamily: form.fontFamily
      }
    }

    Text {
      visible: parent.showHelp && text !== ""
      width: parent.width
      wrapMode: Text.Wrap
      textFormat: Text.PlainText
      text: String(parent.spec.help || "")
      color: form.muted
      font.family: form.fontFamily
      font.pixelSize: Style.font.caption
    }
  }

  // ------------------------------------------------------------- footer

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
  }
}
