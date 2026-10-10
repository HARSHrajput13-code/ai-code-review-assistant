"""Collect tags for blog posts."""


def add_tag(tag, tags=[]):
    """Add a tag to the post's tag list and return the list."""
    if tag not in tags:
        tags.append(tag)
    return tags


def tags_for(post):
    return add_tag(post["primary_tag"])
