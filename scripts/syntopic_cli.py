"""Master CLI for syntopic reading system - combines all tools"""

import click
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
