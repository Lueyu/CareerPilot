import { useEffect, useId, useRef, type ReactNode } from "react";
import { PageHead } from "../../components/PageHead";

const icons = {
  add: new URL("./assets/add.svg", import.meta.url).href,
  back: new URL("./assets/back.svg", import.meta.url).href,
  chevron: new URL("./assets/chevron.svg", import.meta.url).href,
  close: new URL("./assets/close.svg", import.meta.url).href,
  delete: new URL("./assets/delete.svg", import.meta.url).href,
  document: new URL("./assets/document.svg", import.meta.url).href,
  download: new URL("./assets/download.svg", import.meta.url).href,
  folder: new URL("./assets/folder.svg", import.meta.url).href,
  headphones: new URL("./assets/headphones.svg", import.meta.url).href,
  library: new URL("./assets/library-white.svg", import.meta.url).href,
  manage: new URL("./assets/manage.svg", import.meta.url).href,
  more: new URL("./assets/more.svg", import.meta.url).href,
  play: new URL("./assets/play.svg", import.meta.url).href,
  playWhite: new URL("./assets/play-white.svg", import.meta.url).href,
  plus: new URL("./assets/plus.svg", import.meta.url).href,
  plusPurple: new URL("./assets/plus-purple.svg", import.meta.url).href,
  retry: new URL("./assets/retry.svg", import.meta.url).href,
  search: new URL("./assets/search.svg", import.meta.url).href,
  settings: new URL("./assets/settings.svg", import.meta.url).href,
  shuffle: new URL("./assets/shuffle.svg", import.meta.url).href,
};

export function PodcastIcon({ name }: { name: keyof typeof icons }) {
  return (
    <span className="podlib-icon" aria-hidden="true">
      <img src={icons[name]} alt="" />
    </span>
  );
}

export function PodcastHeading({
  title,
  sub,
  back,
  children,
}: {
  title: string;
  sub?: string;
  back?: { label: string; onClick: () => void };
  children?: ReactNode;
}) {
  return (
    <header className="podlib-heading">
      {back && (
        <button
          type="button"
          className="podlib-button podlib-outline podlib-back"
          onClick={back.onClick}
        >
          <PodcastIcon name="back" />
          {back.label}
        </button>
      )}
      <PageHead title={title} sub={sub}>
        {children}
      </PageHead>
    </header>
  );
}

export function PodcastSteps({ step }: { step: 1 | 2 | 3 }) {
  return (
    <ol className="podlib-steps" aria-label="制作步骤">
      {["选择题目", "预览讲稿", "合成音频"].map((label, index) => (
        <li key={label} aria-current={step === index + 1 ? "step" : undefined}>
          {index + 1} {label}
        </li>
      ))}
    </ol>
  );
}

export function PodcastCover() {
  return (
    <div className="podlib-cover" aria-hidden="true">
      <img src={new URL("./assets/cover-art.svg", import.meta.url).href} alt="" />
    </div>
  );
}

export function PodcastDialog({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const element = dialog.current;
    element?.showModal();
    return () => element?.close();
  }, []);
  return (
    <dialog
      ref={dialog}
      className="podlib-dialog"
      aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
    >
      <div className="podlib-panel-head">
        <h2 id={titleId}>{title}</h2>
        <button
          type="button"
          className="podlib-button podlib-neutral podlib-icon-button"
          aria-label="关闭"
          onClick={onClose}
        >
          <PodcastIcon name="close" />
        </button>
      </div>
      {children}
    </dialog>
  );
}
