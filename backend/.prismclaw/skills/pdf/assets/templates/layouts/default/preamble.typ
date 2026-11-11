// ── Package imports ───────────────────────────────────────
#import "@preview/cetz:0.3.4" as cetz
#import "@preview/fletcher:0.5.7" as fletcher

// ── Color definitions ────────────────────────────────────
#let accent = rgb("{{accent}}")
#let accent-lt = rgb("{{accent_lt}}")
#let dark = rgb("{{dark}}")
#let body-text = rgb("{{body_text}}")
#let muted-color = rgb("{{muted}}")

// ── Spacing system ──────────────────────────────────────
// All vertical rhythm derives from these two base tokens.
// Adjust tokens.json to scale the entire document proportionally.
#let section-gap = {{section_gap}}pt   // Major section break (default 26pt)
#let para-gap = {{para_gap}}pt         // Inter-paragraph rhythm (default 8pt)

// ── Page setup ───────────────────────────────────────────
#set page(
  paper: "a4",
  columns: {{columns}},
  margin: (left: {{margin_left_cm}}cm, right: {{margin_right_cm}}cm, top: {{margin_top_cm}}cm, bottom: {{margin_bottom_cm}}cm),
  header: if {{show_header}} {
    context {
      set text(size: {{size_meta}}pt, fill: muted-color)
      upper("{{title}}")
      h(1fr)
      "{{date}}"
      v(para-gap * 0.25)
      line(length: 100%, stroke: 1.5pt + accent)
    }
  },
  footer: if {{show_header}} {
    context {
      line(length: 100%, stroke: 0.5pt + luma(200))
      v(para-gap * 0.125)
      set text(size: {{size_meta}}pt, fill: muted-color)
      "{{author}}"
      h(1fr)
      counter(page).display()
    }
  },
)

// ── Text defaults ────────────────────────────────────────
#set text(
  font: ({{body_font}},),
  size: {{size_body}}pt,
  fill: body-text,
  lang: "{{lang}}",
)
#let heading-font = ({{heading_font}},)
#set par(
  justify: true,
  leading: {{leading}}em,
  spacing: para-gap,
)

// ── Heading styles ───────────────────────────────────────
// Spacing stacks additively (prev.below + next.above).
// Ratio: above ≈ 2× below (heading belongs to content below it).
// Consecutive heading gaps: h1→h2 = 27pt, h2→h3 = 19pt, h3→h4 = 16pt.
#show heading: set block(above: 0pt, below: 0pt)

#show heading.where(level: 1): it => {
  v(section-gap)
  text(font: heading-font, size: {{size_h1}}pt, weight: "bold", fill: dark)[#it.body]
  v(section-gap * 0.5)
}

#show heading.where(level: 2): it => {
  v(para-gap * 0.875)
  text(font: heading-font, size: {{size_h2}}pt, weight: "bold", fill: dark)[#it.body]
  v(para-gap * 0.4375)
}

#show heading.where(level: 3): it => {
  v(para-gap * 0.875)
  text(font: heading-font, size: {{size_h3}}pt, weight: "bold", fill: dark)[#it.body]
  v(para-gap * 0.375)
}

#show heading.where(level: 4): it => {
  v(para-gap * 0.625)
  text(font: heading-font, size: {{size_body}}pt, weight: "bold", fill: dark)[#it.body]
  v(para-gap * 0.3125)
}

#show heading.where(level: 5): it => {
  v(para-gap * 0.5)
  text(font: heading-font, size: {{size_h5}}pt, weight: "bold", fill: dark)[#it.body]
  v(para-gap * 0.25)
}

#show heading.where(level: 6): it => {
  v(para-gap * 0.375)
  text(font: heading-font, size: {{size_h6}}pt, fill: muted-color)[#it.body]
  v(para-gap * 0.1875)
}

// ── Helper functions ─────────────────────────────────────
#let callout-box(body) = {
  v(0pt)
  block(
    width: 100%,
    fill: accent-lt,
    stroke: (left: 4pt + accent),
    radius: 3pt,
    inset: (left: 16pt, right: 10pt, top: 10pt, bottom: 10pt),
    {
      set text(weight: "bold", size: {{size_callout}}pt, fill: dark, hyphenate: true)
      body
    }
  )
  v(0pt)
}

#let fit-math(body) = {
  layout(size => {
    let m = measure(body)
    if m.width > size.width {
      let ratio = size.width / m.width
      align(center, scale(x: ratio * 100%, y: ratio * 100%, origin: center + horizon, body))
    } else {
      body
    }
  })
}

#let caption-text(body) = {
  set text(size: {{size_caption}}pt, fill: muted-color)
  align(center)[#body]
  v(para-gap * 0.375)
}

#let accent-divider() = {
  v(para-gap * 0.875)
  line(length: 100%, stroke: 1.2pt + accent)
  v(para-gap * 0.875)
}

// ── Table styling ────────────────────────────────────────
// Global table style: accent header, alternating rows, light border.
// Python only emits structure (#table(columns:..., ...cells)); styling lives here.
#set table(
  stroke: 0.5pt + luma(200),
  inset: (x: 5pt, y: 4pt),
)
#show table: set block(width: 100%, breakable: true)
#show table.cell: set par(justify: false, leading: 0.5em)
#show table.cell: set text(size: 8pt, hyphenate: true)
#show table.cell.where(y: 0): set text(fill: white, weight: "bold")
#set table(
  fill: (x, y) => if y == 0 { accent } else if calc.rem(y, 2) == 0 { accent-lt } else { none },
)

// ── Code block helper ────────────────────────────────────
#let code-block(code, lang: none) = {
  v(para-gap * 0.5)
  if lang != none and lang != "" {
    text(font: "Courier New", size: 7pt, fill: muted-color)[#upper(lang)]
  }
  block(
    width: 100%,
    breakable: true,
    fill: accent-lt,
    stroke: (left: 3pt + accent, rest: 0.5pt + muted-color),
    inset: (left: 14pt, right: 10pt, top: 8pt, bottom: 8pt),
    radius: 0pt,
    {
      set text(size: 8pt, hyphenate: false)
      set par(justify: false, leading: 0.5em)
      show raw: set text(size: 8pt)
      code
    },
  )
  v(para-gap * 0.5)
}

// ── Block spacing helpers ────────────────────────────────
// block-gap: spacing only — used for breakable blocks (e.g. tables).
#let block-gap(body) = {
  v(para-gap * 0)
  body
  v(para-gap * 0)
}

// figure-block: spacing + breakable:false — used for images, charts, flowcharts.
#let figure-block(body) = {
  v(para-gap * 0)
  block(breakable: false, width: 100%, body)
  v(para-gap * 0)
}

// ── Cover page ──────────────────────────────────────────
#if {{show_cover}} [
  #page(header: none, footer: none, columns: 1)[
    #align(center + horizon)[
      #layout(size => {
        let title-content = text(font: heading-font, size: 54pt, weight: "bold", fill: dark)[{{title}}]
        let m = measure(title-content)
        let max-width = size.width * 0.85
        let max-height = size.height * 0.4
        if m.width > max-width or m.height > max-height {
          let ratio-w = if m.width > max-width { max-width / m.width } else { 1.0 }
          let ratio-h = if m.height > max-height { max-height / m.height } else { 1.0 }
          let ratio = calc.min(ratio-w, ratio-h)
          let new-size = 54pt * ratio
          let final-size = calc.max(new-size, 24pt)
          align(center, text(font: heading-font, size: final-size, weight: "bold", fill: dark)[{{title}}])
        } else {
          align(center, title-content)
        }
      })
      #v(para-gap * 1.25)
      #line(length: 60%, stroke: 2pt + accent)
      #v(para-gap * 1)
      #if "{{author}}" != "" [
        #text(size: 12pt, fill: muted-color)[{{author}}]
        #v(para-gap * 0.5)
      ]
      #if "{{date}}" != "" [
        #text(size: 12pt, fill: muted-color)[{{date}}]
      ]
    ]
  ]
]
