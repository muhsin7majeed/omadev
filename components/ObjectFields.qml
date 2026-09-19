import QtQuick
import qs.Commons
import qs.Ui

// A group of scalar fields for an object: the editor, or one row of a list
// such as a process or a helper app.
//
// With presets, a dropdown picks one and fills the fields; the fields stay
// hidden while a preset is selected, except those flagged `always`, and
// appear for "Custom". Fields flagged `advanced` sit behind "More options".
// A preset whose value is null means "unset", and hides everything but the
// dropdown.
Column {
  id: group

  property var spec: ({})
  property var value: null
  property bool showHelp: false
  property color foreground: Color.popups.text
  property color muted: Qt.alpha(foreground, 0.6)
  property string fontFamily: Style.font.family

  signal changed(var value)

  readonly property var fields: spec.fields || []
  readonly property var presets: spec.presets || []
  readonly property bool hasPresets: presets.length > 0
  readonly property bool hasValue: value !== null && value !== undefined && typeof value === "object"
  readonly property bool custom: !hasPresets || presetValue() === "custom"
  readonly property bool hasAdvanced: fields.some(function(f) { return f.advanced === true })
  property bool showAdvanced: false

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

  // Preset comparison ignores `always` fields (the user's own toggles) and
  // empty values, so a saved preset still reads as that preset.
  function canonical(source) {
    if (source === null || source === undefined || typeof source !== "object") return null
    var out = {}
    var keys = Object.keys(source).sort()
    for (var i = 0; i < keys.length; i++) {
      var key = keys[i]
      var v = source[key]
      if (v === null || v === undefined || v === "" || v === false) continue
      if (Array.isArray(v) && v.length === 0) continue
      if (isAlways(key)) continue
      out[key] = v
    }
    return out
  }

  function isAlways(key) {
    for (var i = 0; i < fields.length; i++) if (fields[i].key === key) return fields[i].always === true
    return false
  }

  function presetValue() {
    var current = JSON.stringify(canonical(value))
    for (var i = 0; i < presets.length; i++) {
      if (JSON.stringify(canonical(presets[i].value === undefined ? null : presets[i].value)) === current) return String(i)
    }
    return "custom"
  }

  function presetOptions() {
    var options = []
    for (var i = 0; i < presets.length; i++) options.push({ value: String(i), label: String(presets[i].label || "") })
    options.push({ value: "custom", label: "Custom" })
    return options
  }

  // Applying a preset keeps the user's `always` fields as they were.
  function applyPreset(next) {
    if (next === "custom") {
      changed(hasValue ? clone(value) : emptyObject())
      return
    }
    var chosen = clone(presets[Number(next)].value)
    if (chosen === null) {
      changed(null)
      return
    }
    for (var i = 0; i < fields.length; i++) {
      var key = fields[i].key
      if (fields[i].always === true && hasValue && value[key] !== undefined) chosen[key] = value[key]
    }
    changed(chosen)
  }

  function fieldVisible(f) {
    if (!hasValue) return false
    if (f.advanced === true && !showAdvanced) return false
    if (f.always === true) return true
    return custom
  }

  width: parent ? parent.width : implicitWidth
  spacing: Style.spacing.sm

  Dropdown {
    visible: group.hasPresets
    width: parent.width
    showLabel: false
    options: group.presetOptions()
    value: group.presetValue()
    foreground: group.foreground
    fontFamily: group.fontFamily
    onChanged: function(next) { group.applyPreset(next) }
  }

  Repeater {
    model: group.fields

    ScalarField {
      required property var modelData
      visible: group.fieldVisible(modelData)
      spec: modelData
      compact: true
      showHelp: group.showHelp
      value: group.hasValue ? group.value[modelData.key] : null
      foreground: group.foreground
      muted: group.muted
      fontFamily: group.fontFamily
      onChanged: function(next) { group.changed(group.withKey(group.value, modelData.key, next)) }
    }
  }

  // "More options" only when the group has advanced fields to reveal and is
  // in a state where fields show at all.
  Text {
    visible: group.hasAdvanced && group.hasValue && (group.custom || group.fields.some(function(f) { return f.advanced === true && f.always === true }))
    textFormat: Text.PlainText
    text: group.showAdvanced ? "Fewer options" : "More options"
    color: moreHover.hovered ? Color.accent : group.muted
    font.family: group.fontFamily
    font.pixelSize: Style.font.caption
    font.underline: moreHover.hovered

    HoverHandler { id: moreHover; cursorShape: Qt.PointingHandCursor }
    TapHandler { onTapped: group.showAdvanced = !group.showAdvanced }
  }
}
