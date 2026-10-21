# Legacy pose folder — fixture

A **fictionalised** stand-in for a hand-made character pose folder, in the shape one actually has
on disk: sixteen numbered stills under `poses/`, six vertical and ten square, plus a README a
human wrote. No real person, no real product, no committed image bytes (§R9, and `.gitignore`
drops `*.png` at depth anyway) — `poses.json` is the manifest and the test materialises the PNGs
into `tmp_path` with a stdlib writer, which is also the mirror of the IHDR reader the importer
uses to derive each ratio.

## The two failure modes this folder is here to reproduce

**Text ordering is not numeric ordering.** `10-smiling.png` sorts before `2-walking-in.png` as a
string. A pose set whose order silently changed is a pose set whose "first reference" moved, so
the importer reads the numeric prefix rather than calling `sorted()`.

**A generated `use` is worse than a blank one.** The descriptions above are what make the set
usable; nothing can derive them from a filename. So the import lands every pose with
`use = "TODO"` and marks the element a draft, and `resolve` refuses a draft — the import is
finished by a human writing sentences, not by the importer guessing them.
