import { useState } from "react";
import { workflows, type WorkflowOperation, type WorkflowRun } from "./model";
import { Card, Icon } from "./ui";

export default function WorkflowCard({
  run,
  busy,
  update,
}: {
  run: WorkflowRun;
  busy: boolean;
  update: (op: WorkflowOperation) => Promise<boolean>;
}) {
  const [editing, setEditing] = useState(false);
  const [step, setStep] = useState(run.current);
  const [label, setLabel] = useState("");
  const name = workflows.find((workflow) => workflow.id === run.id)?.name;
  const current = run.steps[run.current];
  const completed = run.status === "completed";
  const paused = run.status === "paused";
  const summary = completed
    ? "流程已到最后一步。时间线记录完成和跳过的步骤，练习是否掌握仍以作答反馈为准。"
    : paused
      ? "技能已暂停在「" + current + "」。可以修改后续步骤，或者继续当前步骤。"
      : "当前是「" +
        current +
        "」。完成这一环节后再推进，可以保留学习过程。也可以按你的目标调整后续步骤。";

  return (
    <div className="ia-chat-output">
      <p>{summary}</p>
      <Card title={name ?? "技能进度"} detail={completed ? "已结束" : paused ? "已暂停" : "进行中"}>
        <div className="ia-chat-progress-label">
          <span>工作流步骤</span>
          <strong>
            {run.current} / {run.steps.length}
          </strong>
        </div>
        <progress max={run.steps.length} value={run.current} aria-label="技能进度" />
        <ol className="ia-chat-steps">
          {run.steps.map((text, index) => (
            <li key={index} aria-current={index === run.current ? "step" : undefined}>
              <span className="ia-chat-step-mark">
                {index < run.current && !run.skipped.includes(index) ? <Icon name="check" /> : "•"}
              </span>
              <span>{text}</span>
              <small>
                {run.skipped.includes(index)
                  ? "已跳过"
                  : index < run.current
                    ? "已完成"
                    : index === run.current
                      ? "当前"
                      : "待开始"}
              </small>
            </li>
          ))}
        </ol>
        {!completed && (
          <div className="ia-chat-actions">
            {!paused && (
              <button
                disabled={busy}
                className="ia-chat-primary"
                onClick={() => void update({ operation: "advance" })}
              >
                完成本步
              </button>
            )}
            <button
              disabled={busy}
              onClick={() => void update({ operation: paused ? "resume" : "pause" })}
            >
              {paused ? "继续" : "暂停"}
            </button>
            <button disabled={paused || busy} onClick={() => void update({ operation: "skip" })}>
              跳过
            </button>
            <button
              disabled={busy}
              onClick={() => {
                setEditing(!editing);
                setStep(run.current);
                setLabel(current ?? "");
              }}
            >
              修改
            </button>
          </div>
        )}
        {!completed && editing && (
          <form
            className="ia-chat-form"
            onSubmit={(event) => {
              event.preventDefault();
              void update({ operation: "change", step, label: label.trim() }).then((ok) => {
                if (ok) setEditing(false);
              });
            }}
          >
            <label>
              要调整的步骤
              <select
                value={step}
                onChange={(event) => {
                  const next = Number(event.target.value);
                  setStep(next);
                  setLabel(run.steps[next]);
                }}
              >
                {run.steps.map(
                  (text, index) =>
                    index >= run.current && (
                      <option key={index} value={index}>
                        {text}
                      </option>
                    ),
                )}
              </select>
            </label>
            <label>
              新步骤
              <input
                value={label}
                maxLength={120}
                required
                onChange={(event) => setLabel(event.target.value)}
              />
            </label>
            <button disabled={busy || !label.trim()}>保存调整</button>
          </form>
        )}
      </Card>
    </div>
  );
}
