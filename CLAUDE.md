# RTF Tailwind -- Agent Instructions

## What This Project Is

RTF Tailwind is a bidirectional RTF-to-JSON abstraction layer. It decompiles RTF files into structured JSON (document + style registry) and compiles them back. The purpose is to let LLMs edit RTF document content without touching or breaking formatting.

Single file implementation: `rtftailwind.py` (~1100 lines, pure Python, no dependencies).

## File Layout

```
rtftailwind.py                          # All code: parser, walker, classifier, compiler, CLI
Resume - Aditya Joshi - v8.rtf          # Test input (Word-exported CV)
Resume - Aditya Joshi - v8.json         # Decompiled document (83 paragraphs)
Resume - Aditya Joshi - v8_styles.json  # Style registry + RTF structural data
Resume - Aditya Joshi - v8_out.rtf      # Compiled output RTF
```

## How to Edit an RTF (the fast path)

When the user asks you to edit an RTF document (e.g., "update my CV"), use this workflow:

```bash
# Step 1: Produce the LLM-friendly edit view
python rtftailwind.py edit "Resume - Aditya Joshi - v8.rtf"
# -> creates: _edit.json (compact view), .json (full), _styles.json (styles)

# Step 2: Read the _edit.json, make edits, write back
# (see editing rules below)

# Step 3: Apply edits back to RTF
python rtftailwind.py edit "Resume - Aditya Joshi - v8.json" --apply "Resume - Aditya Joshi - v8_edit.json"
# -> creates: _edited.rtf
```

### Page Budget

The edit view includes a `page_info` object at the top:

```json
"page_info": {
  "pages_original": 3,
  "words_original": 1537,
  "words_current": 1361,
  "pages_estimate": 2.7
}
```

Use `pages_estimate` to stay within a page limit. The estimate is based on the original document's words-per-page ratio. Both the `edit` view output and the `--apply` step print the page estimate to the console.

### Editing Rules for the _edit.json

The edit view has two paragraph formats. Use whichever is appropriate:

**Single-style paragraph** (one formatting style for the whole paragraph):
```json
{ "type": "paragraph", "section": "body", "style": "section-title", "text": "Education" }
```

**Multi-style paragraph** (mixed bold/normal or other formatting):
```json
{
  "type": "bullet", "section": "body", "alignment": "justify", "list_id": 11,
  "runs": [
    { "text": "Built ", "style": "garamond-10.5pt" },
    { "text": "a new feature", "style": "bold-garamond-10.5pt" },
    { "text": " using Python.", "style": "garamond-10.5pt" }
  ]
}
```

**What you can do:**
- Edit any `text` field
- Reorder paragraphs (move objects in the array)
- Add new paragraphs using existing styles from `available_styles`
- Remove paragraphs (delete from array)
- Add hyperlinks: `{ "text": "Link", "style": "hyperlink", "url": "https://..." }`
- Convert between single-style and multi-style format as needed

**What you must NOT do:**
- Invent new style names (only use names from `available_styles`)
- Add `_raw_par_fmt` (the apply step restores it automatically)
- Edit the `style_legend` or `available_styles` (they're read-only)

**When adding a new paragraph**, copy `type`, `section`, `alignment`, and `list_id` from an existing paragraph of the same kind. For a new body bullet: use `"type": "bullet", "section": "body", "alignment": "justify", "list_id": 11`.

### Lower-Level Commands

```bash
# Full decompile (includes _raw_par_fmt, for debugging/compiler)
python rtftailwind.py decompile "Resume - Aditya Joshi - v8.rtf"

# Full compile (from full JSON + styles)
python rtftailwind.py compile "Resume - Aditya Joshi - v8.json" "Resume - Aditya Joshi - v8_styles.json"
```

## Code Architecture

The pipeline is: **tokenize -> tree -> analyze -> walk -> classify -> output**.

### Module Map (all in rtftailwind.py)

| Section | Key Functions/Classes | Purpose |
|---|---|---|
| **Constants** | `CP1252_EXTRAS`, `RTF_SPECIAL`, `PARA_CONTROLS`, `CHAR_CONTROLS`, `IGNORE_CONTROLS` | Lookup tables for RTF control word classification |
| **Tokenizer** | `tokenize(raw)` | Scans raw RTF string into `(type, val1, val2)` token tuples |
| **Tree Builder** | `build_tree(tokens)`, `Group`, `CW`, `Text`, `Hex` | Builds recursive node tree from token stream |
| **Raw Helpers** | `find_matching_brace()`, `find_headerr_bounds()`, `find_body_pard()` | Byte-position search in raw RTF for structural splitting |
| **Table Parsers** | `parse_font_table()`, `parse_color_table()` | Extract font/color tables from preamble groups |
| **Tree Analysis** | `analyze_tree()` | Walk top-level tree to find preamble groups, header, body start |
| **Content Walker** | `ContentWalker` class | State-machine walk of RTF tree -> paragraphs with text runs |
| **Style Classifier** | `classify_styles()`, `_state_key()`, `_state_to_controls()`, `_build_style_name()` | Group unique char states into named style classes |
| **Decompile** | `decompile(rtf_path)` | Orchestrates: parse, split, walk, classify -> (doc, styles) |
| **Compile** | `compile_rtf(doc, styles)`, `compile_paragraph()`, `rtf_escape()` | Reassemble RTF from JSON + styles |
| **Edit Workflow** | `make_edit_view()`, `apply_edits()`, `_handle_edit()`, `_describe_style()` | LLM-friendly compact view + merge edits back |
| **CLI** | `main()` | argparse entry point for `decompile`, `compile`, and `edit` commands |

### Data Flow

```
Raw RTF bytes
    |
    v
tokenize() -> list of (type, val, param) tokens
    |
    v
build_tree() -> Group tree (root -> children[0] is the {\rtf1 ...} group)
    |
    +---> analyze_tree() -> font_table, color_table, headerr_group, body_children, doc_defaults
    |
    +---> find_headerr_bounds() + find_body_pard() -> raw string split positions
    |         |
    |         v
    |     preamble (raw string), mid_section (raw string)
    |
    +---> ContentWalker.walk(headerr_group.children) -> header paragraphs
    |
    +---> ContentWalker.walk(body_children) -> body paragraphs
              |
              v
         classify_styles() -> style_registry + paragraphs with named style refs
              |
              v
         document.json + styles.json
```

### Critical Implementation Details

1. **Tree unwrapping.** `build_tree()` returns a wrapper Group. The actual `{\rtf1 ...}` group is at `tree.children[0]`. The `analyze_tree()` function handles this unwrapping.

2. **Paragraph boundaries.** `\pard` resets paragraph formatting and starts a new paragraph. `\par` ends the current paragraph but does NOT reset formatting -- the next paragraph inherits it. This is standard RTF semantics and is critical for correct round-tripping.

3. **Character state tracking.** Each `{group}` pushes the current char state. Closing `}` pops it. The walker maintains a `state_stack` for this. Inside run groups, control words modify the current state. When text is encountered, it's recorded with the current state snapshot.

4. **Style classification.** A style class is identified by a tuple of 14 character formatting properties (font, size, bold, italic, underline, smallcaps, caps, super, sub, strike, color, bg_color, shading, shading_fg). Two runs with identical tuples share a style class.

5. **Compiler isolation.** Each compiled run emits `{\plain<controls> text}`. The `\plain` resets to document defaults, then the style controls set the exact formatting. This prevents any state leaking between runs.

6. **Ignorable destinations.** Groups prefixed with `\*` in RTF are "ignorable destinations." The walker skips: `themedata`, `colorschememapping`, `latentstyles`, `datastore`, `datafield`, `fldinst`, `rsidtbl`, `pgptbl`, and others. Missing any of these causes binary data or metadata to leak into the document text.

7. **Hyperlink handling.** `{\field\fldedit{\*\fldinst HYPERLINK "url"}{\fldrslt {formatted visible_text}}}` -- the walker extracts the URL from fldinst and visible text from fldrslt, producing a run with both `text` and `url` fields.

8. **RTF text escaping.** The compiler escapes `{`, `}`, `\` and encodes non-ASCII as `\'xx` (for byte values <= 255) or `\uN?` (for Unicode > 255).

## How to Edit Document Content (Agent Workflow)

The fastest path for an agent asked to edit an RTF document:

```bash
# 1. Generate edit view
python rtftailwind.py edit input.rtf

# 2. Read input_edit.json, edit it (see rules in "How to Edit an RTF" above)
#    The edit view is ~37KB for an 83-paragraph CV -- fits easily in context

# 3. Write the edited JSON and apply
python rtftailwind.py edit input.json --apply input_edit.json --output result.rtf
```

### Programmatic Example (Python)

```python
import json
from rtftailwind import decompile, make_edit_view, apply_edits, compile_rtf

# Load
doc, styles = decompile('resume.rtf')
view = make_edit_view(doc, styles)

# Edit the view (this is what the LLM does)
for p in view['document']:
    # Simple text replacement works on both formats
    if 'text' in p:
        p['text'] = p['text'].replace('Old Company', 'New Company')
    if 'runs' in p:
        for r in p['runs']:
            r['text'] = r['text'].replace('Old Company', 'New Company')

# Add a new bullet
view['document'].insert(5, {
    'type': 'bullet', 'section': 'body', 'alignment': 'justify', 'list_id': 11,
    'runs': [
        {'text': 'Built ', 'style': 'garamond-10.5pt'},
        {'text': 'a new feature', 'style': 'bold-garamond-10.5pt'},
        {'text': ' using Python.', 'style': 'garamond-10.5pt'},
    ]
})

# Apply and compile
merged = apply_edits(view, doc)
rtf = compile_rtf(merged, styles)
with open('result.rtf', 'wb') as f:
    f.write(rtf.encode('latin-1', errors='replace'))
```

## Testing

The primary correctness check is the **round-trip test**:

```bash
python rtftailwind.py decompile input.rtf
python rtftailwind.py compile input.json input_styles.json
python rtftailwind.py decompile input_out.rtf --doc rt_check.json --styles rt_check_styles.json
# Compare input.json and rt_check.json -- text and style assignments should be identical
```

Current status: **0 text diffs, 0 style diffs** on the test CV.

## Common Pitfalls When Modifying This Code

- **Adding new RTF control words.** If a new RTF file has control words not in `PARA_CONTROLS`, `CHAR_CONTROLS`, or `IGNORE_CONTROLS`, they will be silently stored in `_raw_par_fmt` during the paragraph header phase, or ignored inside run groups. Check the `_on_ctrl` method in `ContentWalker` for the dispatch logic.

- **New ignorable groups.** Word-exported RTF frequently has embedded binary data in `{\*\...}` groups. If the decompiler produces hex garbage or metadata text in the document JSON, a new ignorable destination needs to be added to the skip set in `_on_group()`.

- **Paragraph inheritance.** `_finish_par()` intentionally does NOT reset `par_fmt` or `par_props`. Only `\pard` resets them. Breaking this causes paragraphs to lose their formatting when they don't have an explicit `\pard` prefix.

- **State stack in groups.** The walker pushes/pops `char_state` when entering/leaving groups. Forgetting this causes formatting to leak between runs or entire paragraphs.

- **Space as delimiter.** In RTF, the space after `\controlword ` is consumed as a delimiter, not content. The compiler adds a space between style controls and text in each run group. This space is consumed by the RTF parser and does NOT appear in the text.
