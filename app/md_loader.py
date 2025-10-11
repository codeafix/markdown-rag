from pathlib import Path
import frontmatter
import re

# Expand Obsidian-style [[wikilinks]]
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]]*)?(?:\|([^\]]+))?\]\]")

def _expand_wikilinks(text: str) -> str:
    def _wikirepl(m):
        target = m.group(1)
        alias = m.group(2)
        return (alias or target).replace('-', ' ').replace('_', ' ')
    return WIKILINK_RE.sub(_wikirepl, text)

def load_markdown_docs(vault_dir: str):
    """Yield (text, metadata) for each .md in the Obsidian vault.
    Metadata includes front matter and source path.
    """
    vault = Path(vault_dir)
    for p in vault.rglob("*.md"):
        # Skip .obsidian control directory
        if "/.obsidian/" in str(p):
            continue
        try:
            fm = frontmatter.load(p)
            text = fm.content or ""
            text_norm = _expand_wikilinks(text)

            meta = dict(fm.metadata or {})
            # Common convenience fields for retrieval/citation
            meta.setdefault("title", p.stem.replace('-', ' '))
            meta["source"] = str(p.relative_to(vault))
            yield text_norm, meta
        except Exception:
            continue
