"""
Content renderers for rich chat message display.
Supports markdown, LaTeX formulas, tables, code blocks, and images.
"""

import logging
import re
from typing import Dict, Any, List, Tuple, Optional
from pathlib import Path

logger = logging.getLogger(__name__)

# Check for optional dependencies
import markdown
from markdown.extensions import fenced_code, tables, nl2br, sane_lists

from pygments import highlight
from pygments.lexers import get_lexer_by_name, guess_lexer
from pygments.formatters import HtmlFormatter

import matplotlib
matplotlib.use('Agg')  # Use non-interactive backend
import matplotlib.pyplot as plt
from matplotlib import mathtext
import io
import base64


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

        # Detect LaTeX formulas
        if re.search(r'\$\$[^\$]+\$\$|\$[^\$]+\$', text):
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
                'tables',
                'nl2br',
                'sane_lists',
            ]
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

            # Wrap in div with styling
            return f'<div class="markdown-content">{html}</div>'

        except Exception as e:
            logger.error(f"Markdown rendering failed: {e}")
            # Fallback to plain text
            return f'<div style="white-space: pre-wrap;">{text}</div>'


class FormulaRenderer:
    """Renders LaTeX formulas to images using matplotlib."""

    def __init__(self):
        """Initialize formula renderer."""
        pass

    def render(self, text: str) -> str:
        """
        Render LaTeX formulas in text to embedded images.

        Args:
            text: Text containing LaTeX formulas ($..$ or $$...$$)

        Returns:
            HTML with formulas replaced by images
        """
        try:
            # Find all formulas
            # Display formulas: $$...$$
            text = re.sub(
                r'\$\$([^\$]+)\$\$',
                lambda m: self._render_formula(m.group(1), display=True),
                text
            )

            # Inline formulas: $...$
            text = re.sub(
                r'\$([^\$]+)\$',
                lambda m: self._render_formula(m.group(1), display=False),
                text
            )

            return text

        except Exception as e:
            logger.error(f"Formula rendering failed: {e}")
            return text

    def _render_formula(self, formula: str, display: bool = False) -> str:
        """
        Render a single LaTeX formula to an HTML img tag.

        Args:
            formula: LaTeX formula code
            display: If True, render as display formula (larger, centered)

        Returns:
            HTML img tag with base64-encoded image
        """
        try:
            # Create figure
            fig = plt.figure(figsize=(0.01, 0.01), dpi=100)
            fig.patch.set_facecolor('none')

            # Render formula
            if display:
                fontsize = 14
            else:
                fontsize = 12

            # Parse and render LaTeX
            renderer = fig.canvas.get_renderer()
            t = fig.text(0, 0, f'${formula}$', fontsize=fontsize)

            # Get bounding box
            bbox = t.get_window_extent(renderer=renderer)

            # Adjust figure size to fit formula
            width = bbox.width / fig.dpi
            height = bbox.height / fig.dpi

            plt.close(fig)

            # Create final figure with correct size
            fig = plt.figure(figsize=(width * 1.2, height * 1.2), dpi=100)
            fig.patch.set_facecolor('white')
            ax = fig.add_axes([0, 0, 1, 1])
            ax.axis('off')

            ax.text(0.5, 0.5, f'${formula}$',
                   fontsize=fontsize,
                   ha='center', va='center',
                   transform=ax.transAxes)

            # Save to bytes
            buf = io.BytesIO()
            plt.savefig(buf, format='png', bbox_inches='tight',
                       pad_inches=0.1, transparent=False, facecolor='white')
            plt.close(fig)

            # Encode to base64
            buf.seek(0)
            img_base64 = base64.b64encode(buf.read()).decode('utf-8')

            # Create HTML img tag
            style = 'vertical-align: middle; margin: 0 4px;'
            if display:
                style += ' display: block; margin: 10px auto;'

            return f'<img src="data:image/png;base64,{img_base64}" style="{style}" alt="{formula}"/>'

        except Exception as e:
            logger.error(f"Failed to render formula '{formula}': {e}")
            # Fallback: show formula in code style
            return f'<code>${formula}$</code>'


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

            # 1. Render LaTeX formulas first (before markdown processes them)
            if content_types['latex']:
                text = self.formula_renderer.render(text)

            # 2. Render code blocks (before markdown to preserve them)
            if content_types['code_blocks']:
                text = self.code_renderer.render(text)

            # 3. Render markdown (includes tables)
            if content_types['markdown'] or content_types['tables']:
                text = self.markdown_renderer.render(text)

            # 4. Apply table styling
            if content_types['tables']:
                text = self.table_renderer.render(text)

            # Wrap in styled div with CSS
            css = self._get_combined_css()

            full_html = f"""
            {css}
            <div class="message-content">
                {text}
            </div>
            """

            return full_html

        except Exception as e:
            logger.error(f"Content rendering failed: {e}")
            # Fallback to plain text
            return f'<div style="white-space: pre-wrap;">{text}</div>'

    def _get_combined_css(self) -> str:
        """Get combined CSS for all content types."""
        css_parts = [
            TableRenderer.get_table_css(),
            CodeBlockRenderer.get_code_css(),
            """
            <style>
            .message-content {
                font-family: 'Segoe UI', sans-serif;
                font-size: 9pt;
                color: #212121;
                line-height: 1.6;
            }
            .message-content h1 { font-size: 14pt; margin: 10px 0 5px 0; color: #1565C0; }
            .message-content h2 { font-size: 12pt; margin: 10px 0 5px 0; color: #1976D2; }
            .message-content h3 { font-size: 10pt; margin: 10px 0 5px 0; color: #1E88E5; }
            .message-content p { margin: 5px 0; }
            .message-content ul, .message-content ol { margin: 5px 0; padding-left: 20px; }
            .message-content li { margin: 2px 0; }
            .message-content code {
                background-color: #f5f5f5;
                padding: 2px 4px;
                border-radius: 3px;
                font-family: 'Consolas', monospace;
                font-size: 9pt;
            }
            .message-content pre {
                background-color: #f5f5f5;
                padding: 10px;
                border-radius: 5px;
                overflow-x: auto;
            }
            .message-content blockquote {
                border-left: 4px solid #2196F3;
                padding-left: 10px;
                margin: 10px 0;
                color: #666;
            }
            .message-content a {
                color: #2196F3;
                text-decoration: none;
            }
            .message-content a:hover {
                text-decoration: underline;
            }
            </style>
            """
        ]

        return '\n'.join(css_parts)


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
