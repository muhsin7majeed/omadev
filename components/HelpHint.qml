import QtQuick
import qs.Commons
import qs.Ui

// A small "?" that explains a field on hover. The shell's tooltip is a
// single line, which is right for "Forget network" and wrong for a
// paragraph, so this one wraps at a readable width while keeping the
// shell's tooltip colours and border.
Item {
  id: hint

  property string text: ""
  property color foreground: Color.popups.text
  property string fontFamily: Style.font.family
  property real maxWidth: Style.space(320)

  implicitWidth: glyph.implicitWidth + Style.spacing.xs * 2
  implicitHeight: glyph.implicitHeight
  visible: text !== ""

  Text {
    id: glyph
    anchors.centerIn: parent
    textFormat: Text.PlainText
    // U+F0625, nf-md-help-circle-outline.
    text: "󰘥"
    color: hover.hovered ? Color.accent : Qt.alpha(hint.foreground, 0.55)
    font.family: hint.fontFamily
    font.pixelSize: Style.font.bodySmall
  }

  HoverHandler {
    id: hover
    cursorShape: Qt.WhatsThisCursor
  }

  // The popup takes its width from the content's implicit width, which for
  // a Text is the unwrapped line. So the width is decided here, from the
  // measured text, and the Text is told to wrap inside it.
  TextMetrics {
    id: metrics
    font.family: hint.fontFamily
    font.pixelSize: tip.fontSize
    text: hint.text
  }

  PanelToolTip {
    id: tip
    visible: hover.hovered && hint.text !== ""
    text: hint.text
    fontFamily: hint.fontFamily
    delay: 250

    readonly property real horizontalInset: Border.left(panelBorderSpec) + Border.right(panelBorderSpec) + Style.spacing.controlPaddingX * 2
    contentWidth: Math.min(Math.ceil(metrics.advanceWidth) + horizontalInset, hint.maxWidth)

    contentItem: Text {
      textFormat: Text.PlainText
      text: tip.text
      wrapMode: Text.Wrap
      width: tip.contentWidth
      color: tip.panelForeground
      font.family: tip.fontFamily
      font.pixelSize: tip.fontSize
      leftPadding: Border.left(tip.panelBorderSpec) + Style.spacing.controlPaddingX
      rightPadding: Border.right(tip.panelBorderSpec) + Style.spacing.controlPaddingX
      topPadding: Border.top(tip.panelBorderSpec) + Style.spacing.controlPaddingY
      bottomPadding: Border.bottom(tip.panelBorderSpec) + Style.spacing.controlPaddingY
    }
  }
}
