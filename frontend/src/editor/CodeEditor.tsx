// Monaco wrapper. Monaco is bundled and loaded locally; the CDN is never used (CIS §15.1, D-15).
import Editor, { loader } from "@monaco-editor/react";
import * as monaco from "monaco-editor";
import EditorWorker from "monaco-editor/editor/editor.worker?worker";

self.MonacoEnvironment = { getWorker: () => new EditorWorker() };
loader.config({ monaco });

const OPTIONS: monaco.editor.IStandaloneEditorConstructionOptions = {
  ariaLabel: "Code editor",
  lineNumbers: "on",
  minimap: { enabled: false },
  automaticLayout: true,
  tabSize: 4,
  insertSpaces: true,
  wordWrap: "off",
};

interface Props {
  language: string; // the Monaco language ID
  value: string;
  onChange: (value: string) => void;
}

/** Stays editable during a review (BS §24). */
export function CodeEditor({ language, value, onChange }: Props) {
  return (
    <Editor
      height="60vh"
      language={language}
      value={value}
      onChange={(next) => onChange(next ?? "")}
      options={OPTIONS}
    />
  );
}
