# RTF Tailwind

A semantic style abstraction layer that makes RTF documents safely editable by LLMs.

## The Problem

RTF files contain interleaved formatting and text like `\b\fs28\f1 Aditya Joshi\b0\fs22`. An LLM cannot safely edit this without breaking formatting. RTF Tailwind provides a bidirectional abstraction that separates content from formatting while preserving every byte of the original styling.

## How It Works

Inspired by Tailwind CSS, RTF Tailwind extracts every unique combination of RTF formatting into a named **style class**, then represents the document as structured JSON annotated with those class names. The LLM only ever sees and edits the structured content. A compiler layer maps it back to the exact original RTF control words.

```
  RTF File                    Document JSON + Style Registry                RTF File
 (original)  ── decompile ──>  (LLM edits this)           ── compile ──>  (output)
```

### Three Layers

**Layer 1 -- RTF Parser**: Custom recursive-descent parser that tokenizes raw RTF into a tree of groups, control words, and text nodes. Tracks formatting state through nested group scoping.

**Layer 2 -- Style Class Extractor**: Collects every unique character formatting state across all text runs and assigns a human-readable class name. Heuristics name common patterns (`heading-name`, `body-text`, `section-title`, `hyperlink`) and fall back to descriptive names (`bold-garamond-10.5pt`).

**Layer 3 -- Document Representation**: Clean JSON where each paragraph has typed runs annotated with style class names. Paragraph-level formatting (alignment, spacing, indentation, borders, list membership) lives on the paragraph object. Character formatting lives in the style registry.

## Quick Start — Edit a CV with an LLM

```bash
# 1. Produce an LLM-friendly edit view
python rtftailwind.py edit resume.rtf

# 2. Give resume_edit.json to an LLM, ask it to edit, save its output

# 3. Apply the edits back to RTF
python rtftailwind.py edit resume.json --apply resume_edited.json
```

That's it. The output RTF preserves all original formatting — fonts, sizes, colors, bullet styles, borders, spacing — while containing the LLM's content changes.

### What the LLM Sees

The edit view is a compact JSON (~37 KB for an 83-paragraph CV) with:

- **`page_info`** — word count, character count, and estimated page count (so the LLM can stay within a page budget).
- **`style_legend`** — human-readable descriptions of each style (e.g. "Garamond, 10.5pt, bold").
- **`available_styles`** — the list of style names that can be used in runs.
- **`document`** — the paragraphs. Single-style paragraphs use a flat `text` + `style` shorthand. Multi-style paragraphs use `runs`.

```json
{
  "page_info": {
    "pages_original": 3,
    "words_original": 1537,
    "words_current": 1361,
    "chars_current": 9546,
    "pages_estimate": 2.7
  },
  "style_legend": {
    "heading-name": "Garamond, 16pt, bold, small-caps, colored",
    "garamond-10.5pt": "Garamond, 10.5pt",
    "bold-garamond-10.5pt": "Garamond, 10.5pt, bold"
  },
  "document": [
    {
      "type": "paragraph", "section": "header", "alignment": "center",
      "style": "heading-name", "text": "Aditya Joshi"
    },
    {
      "type": "bullet", "section": "body", "alignment": "justify", "list_id": 11,
      "runs": [
        { "text": "Winner", "style": "bold-garamond-10.5pt" },
        { "text": " of ", "style": "garamond-10.5pt" },
        { "text": "ElevenLabs Hackathon", "style": "bold-garamond-10.5pt" }
      ]
    }
  ]
}
```

Both the `edit` view output and the `--apply` compile step print the page estimate, so you always know where you stand:

```
Edit view: resume_edit.json
  83 paragraphs, 20 styles, 1361 words, ~2.7 pages (was 3)
```

### CLI Reference

```
python rtftailwind.py edit <input.rtf>                          # Produce LLM edit view
python rtftailwind.py edit <doc.json> --apply <edited.json>     # Apply edits -> RTF
python rtftailwind.py decompile <input.rtf>                     # Full decompile (with raw fmt)
python rtftailwind.py compile <doc.json> <styles.json>          # Full compile
```

Default output paths are derived from the input filename.

## Output Format

### document.json

A list of paragraphs, each containing styled text runs:

```json
{
  "document": [
    {
      "type": "paragraph",
      "section": "header",
      "alignment": "center",
      "runs": [
        { "text": "Aditya Joshi", "style": "heading-name" }
      ],
      "_raw_par_fmt": "\\pard\\plain\\ltrpar\\s23\\qc..."
    },
    {
      "type": "bullet",
      "section": "body",
      "alignment": "justify",
      "list_id": 11,
      "runs": [
        { "text": "Winner", "style": "bold-garamond-10.5pt" },
        { "text": " of ", "style": "garamond-10.5pt" },
        { "text": "ElevenLabs Worldwide Hackathon", "style": "bold-garamond-10.5pt" },
        { "text": " (Ireland).", "style": "garamond-10.5pt" }
      ],
      "_raw_par_fmt": "\\pard\\plain\\ltrpar\\s25\\qj\\fi-360\\li360..."
    }
  ]
}
```

**Paragraph fields:**

| Field | Description |
|---|---|
| `type` | `"paragraph"` or `"bullet"` |
| `section` | `"header"` (page header) or `"body"` |
| `alignment` | `"left"`, `"center"`, `"right"`, `"justify"` |
| `space_before`, `space_after` | Spacing in twips |
| `left_indent`, `first_indent` | Indentation in twips |
| `border_bottom`, `border_top` | Boolean, if present |
| `list_id` | List override ID (for bullets/numbered items) |
| `style_num` | RTF stylesheet number |
| `runs` | Array of text runs |
| `_raw_par_fmt` | Raw RTF control words for the compiler |

**Run fields:**

| Field | Description |
|---|---|
| `text` | Plain text content (Unicode) |
| `style` | Style class name (key into `style_registry`) |
| `url` | Hyperlink URL (only on link runs) |

### styles.json

```json
{
  "style_registry": {
    "heading-name": "\\f45\\fs32\\b\\scaps\\cf25",
    "body-text": "\\f37\\fs21\\chcbpat8\\chshdng0\\chcfpat0",
    "section-title": "\\f45\\fs21\\b\\scaps\\cf25",
    "body-bold": "\\f37\\fs21\\b",
    "hyperlink": "\\f46\\fs21\\b\\ul\\cf21\\chcbpat8\\chshdng0\\chcfpat0",
    "garamond-10.5pt": "\\f45\\fs21",
    "bold-garamond-10.5pt": "\\f45\\fs21\\b"
  },
  "font_table": { "0": "Times New Roman", "37": "Calibri", "45": "Garamond" },
  "color_table": [ null, {"red":0,"green":0,"blue":0}, "..." ],
  "doc_defaults": { "font": 37, "font_size": 22 },
  "rtf_preamble": "...raw RTF header preserved verbatim...",
  "rtf_mid_section": "...section transition between page header and body..."
}
```

The `style_registry` maps human-readable class names to their exact RTF control word sequences. Font references (`\f45`) resolve via `font_table`.

## What an LLM Can Safely Do

| Operation | How |
|---|---|
| Edit text | Change the `text` field in any run |
| Reorder paragraphs | Move paragraph objects in the array |
| Add content | Create new runs using existing style class names |
| Remove content | Delete paragraph objects or runs |
| Add hyperlinks | Add `"url"` field to a run with `"hyperlink"` style |

The LLM **cannot** create new formatting -- only reuse existing styles. This is a feature: the output is guaranteed to use only formatting that existed in the original document.

## Round-Trip Fidelity

The system achieves **perfect round-trip at the content level**:

```
decompile(original.rtf) -> JSON
compile(JSON) -> output.rtf
decompile(output.rtf) -> JSON2

JSON == JSON2  (0 text diffs, 0 style diffs)
```

The compiled RTF is structurally simplified (no revision tracking IDs, no bidirectional markup) but visually identical when opened in Word or LibreOffice. The original RTF preamble (font tables, color tables, list definitions, document settings) is preserved verbatim.

## Architecture

```
rtftailwind.py
 |
 |-- Tokenizer        Scans raw RTF bytes into tokens ({, }, \control, text, \'hex)
 |-- Tree Builder      Builds recursive Group/ControlWord/Text/Hex node tree
 |-- Raw Splitter      Finds preamble/header/body boundaries via brace matching
 |-- Font/Color Parser Extracts font_table and color_table from preamble groups
 |-- Content Walker    Walks tree with formatting state stack, emits paragraphs + runs
 |-- Style Classifier  Groups unique char states into named classes with heuristics
 |-- Compiler          Reassembles RTF from JSON + styles + preserved preamble
```

### Key Design Decisions

- **No dependencies.** Pure Python stdlib. No `rtfparse`, `striprtf`, or other libraries (they don't support round-tripping).
- **Raw preamble preservation.** Everything before the first body paragraph (font tables, color tables, stylesheets, list definitions, document properties) is stored as a raw string and emitted verbatim. This guarantees structural fidelity.
- **`\plain` isolation.** Each compiled run group starts with `\plain` to reset character formatting to document defaults, then applies the style class controls. This prevents formatting leaks between runs.
- **Paragraph format inheritance.** When `\par` ends a paragraph without a following `\pard`, the next paragraph inherits the same formatting. This matches RTF semantics.
- **Ignorable destination skipping.** Theme data, datastore blobs, latent style lists, revision tables, and other non-content groups are skipped during content extraction.

## Requirements

- Python 3.8+
- No external dependencies
