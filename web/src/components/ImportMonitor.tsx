/**
 * 全局面经导入进度悬浮窗(挂在 Layout, 所有页面可见)。
 *
 * 有进行中的导入时右下角显示进度条; 点击跳到面经库;
 * 完成/失败时 toast 提示并广播 cp-import-finished 事件(面经库页监听刷新)。
 */
import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAppState } from './AppState';
import { ImportFinished, TrackedImport, onImportFinished, subscribeImports } from '../lib/importTracker';

export default function ImportMonitor() {
  const [list, setList] = useState<TrackedImport[]>([]);
  const { toast } = useAppState();
  const navigate = useNavigate();

  useEffect(() => subscribeImports(setList), []);
  useEffect(() => onImportFinished((r: ImportFinished) => {
    window.dispatchEvent(new CustomEvent('cp-import-finished', { detail: r }));
    if (r.status === 'REVIEW') {
      toast('✅', `面经解析完成：${r.itemCount} 道题待审核（面经库）`);
    } else if (r.status === 'FAILED') {
      toast('⚠️', `面经解析失败：${r.error || '未知错误'}`);
    } else if (r.status === 'TIMEOUT') {
      toast('⏳', '解析耗时较长，稍后可在「面经库 → 待审核」查看结果');
    }
  }), [toast]);

  if (!list.length) return null;
  return (
    <div className="import-monitor">
      {list.map((t) => (
        <div
          key={t.id}
          className="import-monitor-item"
          onClick={() => navigate('/bank')}
          title="点击前往面经库"
        >
          <span className="import-monitor-spin" />
          <div className="import-monitor-txt">
            <b>正在解析面经 {t.shortCode}</b>
            <span>{t.note || '后台解析中'} · {(t.elapsedMs / 1000).toFixed(0)}s</span>
          </div>
          <div className="import-monitor-bar">
            <i style={{ width: `${Math.min(100, Math.round((t.step / Math.max(1, t.total)) * 100))}%` }} />
          </div>
        </div>
      ))}
    </div>
  );
}
