/**
 * A diagram the assistant wrote as Mermaid text, drawn.
 *
 * The library is large and most chats never need it, so it is fetched
 * the first time a message holds a diagram. It is run at its strict
 * level: the text comes from a model, so nothing in it may carry
 * markup, a script or a link that acts. Text that is not a diagram
 * (a reply still being written, a mistake) draws nothing, and the
 * message keeps showing it as the code it is.
 */

let drawn = 0;
let library: Promise<typeof import('mermaid').default> | null = null;

function mermaid(): Promise<typeof import('mermaid').default> {
  library ??= import('mermaid').then((module) => module.default);
  return library;
}

/** The colours a chart's parts take, in turn: the accent first, then
 *  hues that stand apart from it and from each other. */
const SERIES = ['#059669', '#0ea5e9', '#8b5cf6', '#f59e0b', '#ec4899', '#14b8a6', '#6366f1', '#84cc16'];

/** The app's own colours, as the library names them. */
function palette(dark: boolean): Record<string, unknown> {
  const ink = dark ? '#f3f2ef' : '#171614';
  const quiet = dark ? '#a8a49b' : '#5b5850';
  const line = dark ? '#403e39' : '#d6d3cc';
  const paper = dark ? '#171614' : '#ffffff';
  const colours: Record<string, unknown> = {
    darkMode: dark,
    background: paper,
    primaryColor: dark ? '#143d31' : '#d9f2e8',
    primaryTextColor: ink,
    primaryBorderColor: dark ? '#10b981' : '#047857',
    secondaryColor: dark ? '#1f1e1b' : '#f3f2ef',
    tertiaryColor: paper,
    lineColor: quiet,
    textColor: ink,
    titleColor: ink,
    pieTitleTextColor: ink,
    pieSectionTextColor: '#ffffff',
    pieLegendTextColor: ink,
    pieStrokeColor: paper,
    pieOuterStrokeColor: line,
    pieOpacity: '1',
  };
  SERIES.forEach((colour, at) => (colours[`pie${at + 1}`] = colour));
  return {
    ...colours,
    xyChart: {
      backgroundColor: paper, titleColor: ink, xAxisLabelColor: quiet, yAxisLabelColor: quiet,
      xAxisTitleColor: quiet, yAxisTitleColor: quiet, xAxisLineColor: line, yAxisLineColor: line,
      xAxisTickColor: line, yAxisTickColor: line, plotColorPalette: SERIES.join(','),
    },
  };
}

/** The diagram as SVG, or null when the text does not draw. */
export async function drawDiagram(source: string, dark: boolean): Promise<string | null> {
  const text = source.trim();
  if (!text) return null;
  try {
    const draw = await mermaid();
    draw.initialize({
      startOnLoad: false,
      securityLevel: 'strict',
      theme: 'base',
      fontFamily: getComputedStyle(document.body).fontFamily,
      themeVariables: palette(dark),
    });
    if (!(await draw.parse(text, { suppressErrors: true }))) return null;
    const { svg } = await draw.render(`chat-diagram-${++drawn}`, text);
    return svg;
  } catch {
    return null;
  }
}
