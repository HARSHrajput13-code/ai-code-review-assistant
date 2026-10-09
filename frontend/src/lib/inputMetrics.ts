// Input size, measured exactly as the server measures it (CIS §7.2, §15.3).

/** UTF-8 bytes of the text as submitted. */
export const byteLength = (text: string): number => new TextEncoder().encode(text).length;

/** Lines after line-ending normalization: CRLF and lone CR count as LF. */
export const lineCount = (text: string): number => text.replace(/\r\n?/g, "\n").split("\n").length;
