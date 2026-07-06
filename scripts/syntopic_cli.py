"""Master CLI for syntopic reading system - combines all tools"""

import click
import sys as _sys, pathlib as _pl
_ROOT = str(_pl.Path(__file__).resolve().parent.parent)
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)  # so `import src` works run-from-repo without `pip install -e .`

from src.cli import topic_search, chunk_browser, tree_navigator


@click.group()
@click.version_option(version="1.0.0")
def cli():
    """
    Syntopic Reading System CLI

    Tools for navigating topics, chunks, and hierarchical structure.
    Designed for Claude Code agent interaction with semi-XML output.
    """
    pass


# Add subcommands from each CLI module
cli.add_command(topic_search.cli, name="topics")
cli.add_command(chunk_browser.cli, name="chunks")
cli.add_command(tree_navigator.cli, name="tree")


if __name__ == "__main__":
    cli()
