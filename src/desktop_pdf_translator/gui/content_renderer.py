"""
Content renderers for rich chat message display.
Supports markdown, LaTeX formulas (via KaTeX), tables, code blocks, and images.
"""

import logging
import re
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

# Check for optional dependencies
import markdown
from markdown.extensions import fenced_code, tables, nl2br, sane_lists

from pygments import highlight
from pygments.lexers import get_lexer_by_name, guess_lexer
from pygments.formatters import HtmlFormatter


class ContentDetector:
    """Detects different types of content in text."""

    @staticmethod
    def detect_content_types(text: str) -> Dict[str, bool]:
        """
        Detect what types of content are present in the text.

        Returns:
            Dictionary with detected content types
        """
        detected = {
            'markdown': False,
            'latex': False,
            'code_blocks': False,
            'tables': False,
            'images': False,
        }

        # Detect LaTeX formulas - support multiple delimiter formats
        # $...$ or $$...$$ (standard)
        # \[...\] or \(...\) (LaTeX style)
        # [... ] on separate lines (some markdown flavors)
        if re.search(r'\$\$[^\$]+\$\$|\$[^\$]+\$|\\\[[\s\S]+?\\\]|\\\([\s\S]+?\\\)|^\s*\[[\s\S]+?\]\s*$', text, re.MULTILINE):
            detected['latex'] = True

        # Detect code blocks
        if re.search(r'```[\s\S]*?```|`[^`]+`', text):
            detected['code_blocks'] = True

        # Detect markdown tables
        if re.search(r'\|[^\n]+\|', text):
            detected['tables'] = True

        # Detect markdown formatting
        if re.search(r'#{1,6}\s|[\*_]{1,2}[^\*_]+[\*_]{1,2}|\[.+\]\(.+\)', text):
            detected['markdown'] = True

        # Always enable markdown if other content types are detected
        if any([detected['latex'], detected['code_blocks'], detected['tables']]):
            detected['markdown'] = True

        return detected


class MarkdownRenderer:
    """Renders markdown text to HTML."""

    def __init__(self):
        """Initialize markdown renderer."""
        # Configure markdown with extensions
        self.md = markdown.Markdown(
            extensions=[
                'fenced_code',
                'codehilite',  # Syntax highlighting with pygments
                'tables',
                'nl2br',
                'sane_lists',
            ],
            extension_configs={
                'codehilite': {
                    'css_class': 'highlight',
                    'linenums': False,
                    'guess_lang': True,
                }
            }
        )

    def render(self, text: str) -> str:
        """
        Render markdown text to HTML.

        Args:
            text: Markdown text

        Returns:
            HTML string
        """
        try:
            # Convert markdown to HTML
            html = self.md.convert(text)

            # Reset markdown state for next conversion
            self.md.reset()

            # Return HTML directly without extra wrapper
            return html

        except Exception as e:
            logger.error(f"Markdown rendering failed: {e}")
            # Fallback to plain text
            return f'<div style="white-space: pre-wrap;">{text}</div>'


class FormulaRenderer:
    """Renders LaTeX formulas using KaTeX (client-side JavaScript rendering)."""

    def __init__(self):
        """Initialize formula renderer."""
        pass

    def render(self, text: str) -> str:
        """
        Prepare LaTeX formulas for KaTeX rendering.
        Converts various LaTeX delimiter formats to KaTeX-compatible delimiters.

        Args:
            text: Text containing LaTeX formulas (various delimiter formats)

        Returns:
            Text with normalized LaTeX delimiters for KaTeX
        """
        try:
            # Normalize all delimiter formats to KaTeX standard ($$...$$ and $...$)

            # 1. Display formulas: [...] on separate lines → $$...$$
            text = re.sub(
                r'^\s*\[([\s\S]+?)\]\s*$',
                lambda m: f'$${self._clean_formula(m.group(1))}$$',
                text,
                flags=re.MULTILINE
            )

            # 2. Display formulas: \[...\] → $$...$$
            text = re.sub(
                r'\\\[([\s\S]+?)\\\]',
                lambda m: f'$${self._clean_formula(m.group(1))}$$',
                text
            )

            # 3. Inline formulas: \(...\) → $...$
            text = re.sub(
                r'\\\(([\s\S]+?)\\\)',
                lambda m: f'${self._clean_formula(m.group(1))}$',
                text
            )

            # 4. Clean existing $$...$$ and $...$ formulas
            text = re.sub(
                r'\$\$([^\$]+)\$\$',
                lambda m: f'$${self._clean_formula(m.group(1))}$$',
                text
            )

            text = re.sub(
                r'\$([^\$]+)\$',
                lambda m: f'${self._clean_formula(m.group(1))}$',
                text
            )

            return text

        except Exception as e:
            logger.error(f"Formula normalization failed: {e}")
            return text

    def _clean_formula(self, formula: str) -> str:
        """
        Clean up LaTeX formula text.

        Args:
            formula: Raw LaTeX formula code

        Returns:
            Cleaned LaTeX formula
        """
        # Strip whitespace
        formula = formula.strip()

        # Remove literal \n characters that might appear in the formula
        formula = formula.replace('\\n', ' ')

        # Handle double-escaped backslashes (\\text → \text)
        # This can happen if the text comes from JSON or is double-escaped
        if '\\\\' in formula:
            formula = formula.replace('\\\\', '\\')

        return formula


class TableRenderer:
    """Renders markdown tables with enhanced styling."""

    def render(self, text: str) -> str:
        """
        Enhance table rendering with custom CSS.

        Args:
            text: HTML text (from markdown) containing tables

        Returns:
            HTML with styled tables
        """
        # Markdown already converts tables to HTML
        # We just need to add CSS classes

        # Add class to tables
        text = re.sub(
            r'<table>',
            '<table class="styled-table">',
            text
        )

        return text

    @staticmethod
    def get_table_css() -> str:
        """Get CSS for table styling."""
        return """
        <style>
        .styled-table {
            border-collapse: collapse;
            margin: 15px 0;
            font-size: 9pt;
            font-family: 'Segoe UI', sans-serif;
            min-width: 400px;
            box-shadow: 0 0 10px rgba(0, 0, 0, 0.05);
            width: 100%;
        }
        .styled-table thead tr {
            background-color: #2196F3;
            color: #ffffff;
            text-align: left;
        }
        .styled-table th,
        .styled-table td {
            padding: 8px 12px;
            border: 1px solid #dddddd;
        }
        .styled-table tbody tr {
            border-bottom: 1px solid #dddddd;
        }
        .styled-table tbody tr:nth-of-type(even) {
            background-color: #f9f9f9;
        }
        .styled-table tbody tr:hover {
            background-color: #f1f1f1;
        }
        .styled-table tbody tr:last-of-type {
            border-bottom: 2px solid #2196F3;
        }
        </style>
        """


class CodeBlockRenderer:
    """Renders code blocks with syntax highlighting."""

    def __init__(self):
        """Initialize code block renderer."""
        pass

    def render(self, text: str) -> str:
        """
        Render code blocks with syntax highlighting.

        Args:
            text: Text containing code blocks (```language ... ```)

        Returns:
            HTML with highlighted code
        """
        try:
            # Find all code blocks
            pattern = r'```(\w+)?\n([\s\S]*?)```'

            def replace_code_block(match):
                language = match.group(1) or 'text'
                code = match.group(2)
                return self._render_code_block(code, language)

            text = re.sub(pattern, replace_code_block, text)

            return text

        except Exception as e:
            logger.error(f"Code block rendering failed: {e}")
            return text

    def _render_code_block(self, code: str, language: str) -> str:
        """
        Render a single code block with syntax highlighting.

        Args:
            code: Code text
            language: Programming language

        Returns:
            HTML with highlighted code
        """
        try:
            # Get lexer for language
            try:
                lexer = get_lexer_by_name(language, stripall=True)
            except:
                lexer = guess_lexer(code)

            # Format with HTML formatter
            formatter = HtmlFormatter(
                style='friendly',
                linenos=False,
                cssclass='code-block',
                prestyles='background: #f5f5f5; padding: 10px; border-radius: 5px; overflow-x: auto;'
            )

            highlighted = highlight(code, lexer, formatter)

            # Add language badge and copy button placeholder
            return f'''
            <div class="code-container" style="margin: 10px 0;">
                <div class="code-header" style="background: #e0e0e0; padding: 5px 10px; border-radius: 5px 5px 0 0; display: flex; justify-content: space-between; align-items: center;">
                    <span style="font-weight: bold; font-size: 8pt; color: #666;">{language.upper()}</span>
                    <span style="font-size: 8pt; color: #666; cursor: pointer;" title="Copy code">[Copy]</span>
                </div>
                {highlighted}
            </div>
            '''

        except Exception as e:
            logger.error(f"Failed to highlight code: {e}")
            # Fallback: plain code block
            return f'<pre style="background: #f5f5f5; padding: 10px; border-radius: 5px; overflow-x: auto;"><code>{code}</code></pre>'

    @staticmethod
    def get_code_css() -> str:
        """Get additional CSS for code blocks."""
        return """
        <style>
        .code-block {
            font-family: 'Consolas', 'Courier New', monospace;
            font-size: 9pt;
            line-height: 1.5;
        }
        .code-container {
            border: 1px solid #ddd;
            border-radius: 5px;
            overflow: hidden;
        }
        </style>
        """


class ContentRenderer:
    """Main content renderer that combines all rendering capabilities."""

    def __init__(self):
        """Initialize content renderer with all sub-renderers."""
        self.detector = ContentDetector()
        self.markdown_renderer = MarkdownRenderer()
        self.formula_renderer = FormulaRenderer()
        self.table_renderer = TableRenderer()
        self.code_renderer = CodeBlockRenderer()

    def render(self, text: str) -> str:
        """
        Render text with all applicable content types.

        Args:
            text: Raw text (may contain markdown, LaTeX, code, etc.)

        Returns:
            Fully rendered HTML
        """
        try:
            # Detect content types
            content_types = self.detector.detect_content_types(text)

            logger.info(f"Detected content types: {content_types}")

            # Apply renderers in order

            # 1. Render LaTeX formulas first (normalize delimiters for KaTeX)
            if content_types['latex']:
                text = self.formula_renderer.render(text)

            # 2. Render markdown (includes code blocks via fenced_code extension and tables)
            if content_types['markdown'] or content_types['code_blocks'] or content_types['tables']:
                text = self.markdown_renderer.render(text)

            # 3. Apply table styling to markdown-generated tables
            if content_types['tables']:
                text = self.table_renderer.render(text)

            # Note: Code blocks are handled by markdown's fenced_code extension
            # No separate code rendering needed - markdown outputs <pre><code> which we style with CSS

            # Wrap in proper HTML document with CSS and KaTeX
            css = self._get_combined_css()

            full_html = f"""<!DOCTYPE html>
            <html>
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                {css}
            </head>
            <body>
                {text}
            </body>
            </html>
            """

            return full_html

        except Exception as e:
            logger.error(f"Content rendering failed: {e}")
            # Fallback to plain text
            return f'<div style="white-space: pre-wrap;">{text}</div>'

    def _get_combined_css(self) -> str:
        """Get combined CSS for all content types including KaTeX."""
        css_parts = [
            self._get_katex_resources(),
            TableRenderer.get_table_css(),
            CodeBlockRenderer.get_code_css(),
            """
            <style>
            /* Body styling to match chat bubble background */
            html, body {
                font-family: 'Segoe UI', sans-serif;
                font-size: 9pt;
                color: #212121;
                line-height: 1.6;
                margin: 0;
                padding: 0;  /* No padding - MessagePanel provides padding */
                background: #F5F5F5;
                overflow: hidden;  /* Disable scrollbars */
                width: 100%;
                height: auto;
            }
            h1 { font-size: 14pt; margin: 10px 0 5px 0; color: #1565C0; }
            h2 { font-size: 12pt; margin: 10px 0 5px 0; color: #1976D2; }
            h3 { font-size: 10pt; margin: 10px 0 5px 0; color: #1E88E5; }
            p { margin: 5px 0; }
            ul, ol { margin: 5px 0; padding-left: 20px; }
            li { margin: 2px 0; }
            code {
                background-color: #f5f5f5;
                padding: 2px 4px;
                border-radius: 3px;
                font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
                font-size: 9pt;
            }
            pre {
                background-color: #f5f5f5;
                padding: 15px;
                border-radius: 5px;
                overflow-x: auto;
                border: 1px solid #e0e0e0;
            }
            pre code {
                background: none;
                padding: 0;
                font-size: 9pt;
                line-height: 1.5;
            }
            /* Pygments syntax highlighting (codehilite) */
            .highlight {
                background: #f5f5f5;
                border-radius: 5px;
                padding: 15px;
                margin: 10px 0;
                overflow-x: auto;
                border: 1px solid #e0e0e0;
            }
            .highlight pre {
                margin: 0;
                padding: 0;
                background: none;
                border: none;
            }
            /* Pygments token colors */
            .highlight .k { color: #0000FF; font-weight: bold; } /* Keyword */
            .highlight .s { color: #008000; } /* String */
            .highlight .c { color: #808080; font-style: italic; } /* Comment */
            .highlight .n { color: #000000; } /* Name */
            .highlight .o { color: #666666; } /* Operator */
            .highlight .p { color: #000000; } /* Punctuation */
            .highlight .nf { color: #795E26; } /* Function name */
            .highlight .nb { color: #0000FF; } /* Built-in */
            .highlight .nc { color: #008080; font-weight: bold; } /* Class name */
            blockquote {
                border-left: 4px solid #2196F3;
                padding-left: 10px;
                margin: 10px 0;
                color: #666;
            }
            a {
                color: #2196F3;
                text-decoration: none;
            }
            a:hover {
                text-decoration: underline;
            }
            /* KaTeX formula styling */
            .katex { font-size: 1.0em; }
            .katex-display { margin: 10px 0; }
            </style>
            """
        ]

        return '\n'.join(css_parts)

    def _get_katex_resources(self) -> str:
        """Get KaTeX CSS and JavaScript resources."""
        return """
        <!-- KaTeX CSS -->
        <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css"
              integrity="sha384-n8MVd4RsNIU0tAv4ct0nTaAbDJwPJzDEaqSD1odI+WdtXRGWt2kTvGFasHpSy3SV"
              crossorigin="anonymous">

        <!-- KaTeX JavaScript -->
        <script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js"
                integrity="sha384-XjKyOOlGwcjNTAIQHIpgOno0Hl1YQqzUOEleOLALmuqehneUG+vnGctmUb0ZY0l8"
                crossorigin="anonymous"></script>

        <!-- KaTeX auto-render extension -->
        <script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/contrib/auto-render.min.js"
                integrity="sha384-+VBxd3r6XgURycqtZ117nYw44OOcIax56Z4dCRWbxyPt0Koah1uHoK0o4+/RRE05"
                crossorigin="anonymous"
                onload="renderMathInElement(document.body, {
                    delimiters: [
                        {left: '$$', right: '$$', display: true},
                        {left: '$', right: '$', display: false}
                    ],
                    throwOnError: false
                });"></script>
        """


# Convenience function for external use
def render_content(text: str) -> str:
    """
    Render text content with all applicable formatting.

    Args:
        text: Raw text content

    Returns:
        Rendered HTML
    """
    renderer = ContentRenderer()
    return renderer.render(text)
