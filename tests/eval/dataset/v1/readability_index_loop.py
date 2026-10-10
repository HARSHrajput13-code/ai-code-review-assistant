"""Print a numbered list of attendees."""


def numbered(attendees):
    lines = []
    for i in range(len(attendees)):
        lines.append(str(i + 1) + ". " + attendees[i])
    return "\n".join(lines)
