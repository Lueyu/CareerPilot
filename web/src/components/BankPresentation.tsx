import { useRef, useState, type ReactNode } from 'react';

export function BankSearch({ value, onChange, maxLength }: { value: string; onChange: (value: string) => void; maxLength?: number }) {
  return (
    <input
      className="inp bank-search"
      aria-label="搜索我的题目"
      placeholder="搜索题目关键词…"
      value={value}
      maxLength={maxLength}
      onChange={(event) => onChange(event.target.value)}
    />
  );
}

export function BankQuestionRow({ id, title, metadata, actions, children }: {
  id?: string | number;
  title: string;
  metadata: ReactNode;
  actions: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div className="q-card">
      {id !== undefined && <div className="q-id">{id}</div>}
      <div className="q-body">
        <div className="q-title">{title}</div>
        <div className="q-meta">{metadata}</div>
      </div>
      <div className="q-actions">{actions}</div>
      {children && <div className="q-extra stack">{children}</div>}
    </div>
  );
}

export function BankUploadPicker({ accept, description, disabled, multiple, onFiles }: {
  accept: string;
  description: string;
  disabled?: boolean;
  multiple?: boolean;
  onFiles: (files: FileList | null) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);

  function pick() {
    if (!disabled) input.current?.click();
  }

  return (
    <div
      className={`dropzone ${dragOver && !disabled ? 'dragover' : ''}`}
      role="button"
      aria-disabled={disabled}
      tabIndex={disabled ? -1 : 0}
      onClick={pick}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          pick();
        }
      }}
      onDragOver={(event) => {
        event.preventDefault();
        event.stopPropagation();
        if (!disabled) setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(event) => {
        event.preventDefault();
        event.stopPropagation();
        setDragOver(false);
        if (!disabled) onFiles(event.dataTransfer.files);
      }}
    >
      <div className="dropzone-ic">📥</div>
      <b>拖拽文件到这里，或点击选择</b>
      <p>{description}</p>
      <input
        ref={input}
        type="file"
        aria-label="选择面经文件"
        accept={accept}
        multiple={multiple}
        disabled={disabled}
        hidden
        onClick={(event) => event.stopPropagation()}
        onChange={(event) => {
          onFiles(event.target.files);
          event.target.value = '';
        }}
      />
    </div>
  );
}
