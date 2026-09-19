import QtQuick
import qs.Commons
import qs.Ui

// A group of scalar fields for an `object` schema field, such as the editor.
// When the schema offers presets, a dropdown picks one and fills the fields;
// editing any field afterwards turns the selection into "Custom". A preset
// whose value is null means "unset", and hides the fields.
Column {
  id: group

  property var spec: ({})
  property var value: null
  property color foreground: Color.popups.text
  property color muted: Qt.alpha(foreground, 0.6)
  property string fontFamily: Style.font.family

  signal changed(var value)

  readonly property var fields: spec.fields || []
  readonly property var presets: spec.presets || []
  readonly property bool hasValue: value !== null && value !== undefined && typeof value === "object"

  function clone(source) {
    return source === null || source === undefined ? null : JSON.parse(JSON.stringify(source))
  }

  function emptyObject() {
    var out = {}
    for (var i = 0; i < fields.length; i++) out[fields[i].key] = ""
    return out
  }

  function withKey(source, key, next) {
    var out = source && typeof source === "object" ? clone(source) : emptyObject()
    out[key] = next
    return out
  }

  // Which preset the current value equals, or "custom".
  function presetValue() {
    var current = JSON.stringify(hasValue ? value : null)
    for (var i = 0; i < presets.length; i++) {
      if (JSON.stringify(presets[i].value === undefined ? null : presets[i].value) === current) return String(i)
    }
    return "custom"
  }

  function presetOptions() {
    var options = []
    for (var i = 0; i < presets.length; i++) options.push({ value: String(i), label: String(presets[i].label || "") })
    options.push({ value: "custom", label: "Custom" })
    return options
  }

  width: parent ? parent.width : implicitWidth
  spacing: Style.spacing.sm

  Dropdown {
    visible: group.presets.length > 0
    width: parent.width
    showLabel: false
    options: group.presetOptions()
    value: group.presetValue()
    foreground: group.foreground
    fontFamily: group.fontFamily
    onChanged: function(next) {
      if (next === "custom") group.changed(group.hasValue ? group.clone(group.value) : group.emptyObject())
      else group.changed(group.clone(group.presets[Number(next)].value))
    }
  }

  Column {
    visible: group.hasValue || group.presets.length === 0
    width: parent.width
    spacing: Style.spacing.sm

    Repeater {
      model: group.fields

      ScalarField {
        required property var modelData
        spec: modelData
        compact: true
        value: group.hasValue ? group.value[modelData.key] : null
        foreground: group.foreground
        muted: group.muted
        fontFamily: group.fontFamily
        onChanged: function(next) { group.changed(group.withKey(group.value, modelData.key, next)) }
      }
    }
  }
}
