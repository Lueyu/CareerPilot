import { useRef } from 'react';
import { createRoot } from 'react-dom/client';
import { useScrollbarGutter } from '../src/lib/useScrollbarGutter.ts';
import '../src/styles/scrollGutter.css';
import '../src/interview/chat/chat.css';
import '../src/pages/studyChat.css';

function ChatShell() {
  const shell = useRef<HTMLElement>(null);
  const scroll = useRef<HTMLDivElement>(null);
  useScrollbarGutter(shell, scroll);
  return <section ref={shell} className="ia-chat cp-scroll-shell" data-case="chat" data-compensated="true">
    <div ref={scroll} className="ia-chat-scroll cp-scroll-area">
      <div className="ia-chat-content" data-measure="column">
        <header className="ia-chat-heading"><div><h1>对齐基线</h1><p>合成会话</p></div></header>
        <div className="ia-chat-card" data-measure="card"><h3>消息卡片</h3><p>用于核对顶部卡片列与底部输入框的左右边界。</p></div>
      </div>
    </div>
    <div className="ia-chat-composer" data-measure="fixed">
      <div className="ia-chat-composer-box"><textarea aria-label="输入" /></div>
    </div>
  </section>;
}

function StudyShell() {
  const shell = useRef<HTMLDivElement>(null);
  const scroll = useRef<HTMLDivElement>(null);
  useScrollbarGutter(shell, scroll);
  return <div ref={shell} className="study-page coach-session cp-scroll-shell" data-case="study" data-compensated="true">
    <header className="coach-session-head">
      <div className="coach-head-inner" data-measure="head"><div className="coach-title"><h1>学习会话</h1></div></div>
    </header>
    <div ref={scroll} className="coach-scroll cp-scroll-area">
      <div className="coach-inner" data-measure="column">
        <section className="study-thread">
          <div className="study-turn assistant"><h4>学习助手</h4><p>用于核对消息列与头部、输入栏的左右边界。</p></div>
        </section>
      </div>
    </div>
    <footer className="coach-compose" data-measure="fixed">
      <div className="coach-input"><textarea aria-label="输入" /></div>
    </footer>
  </div>;
}

createRoot(document.getElementById('root')!).render(<>
  <div className="fixture-frame"><ChatShell /></div>
  <div className="fixture-frame"><StudyShell /></div>
</>);
