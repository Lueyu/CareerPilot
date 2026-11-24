import { t as translate, useT } from '../i18n';
/**
 * 全局面经导入进度悬浮窗(挂在 Layout, 所有页面可见)。
 *
 * 有进行中的导入时右下角显示进度条; 点击跳到面试题库;
 * 完成/失败时 toast 提示并广播 cp-import-finished 事件(面试题库页监听刷新)。
 */
import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAppState } from './AppState';
import { ImportFinished, TrackedImport, importProgressNote, onImportFinished, subscribeImports } from '../lib/importTracker';

export default function ImportMonitor() {
  const t = useT();
  const [list, setList] = useState<TrackedImport[]>([]);
  const { toast } = useAppState();
  const navigate = useNavigate();

  useEffect(() => subscribeImports(setList), []);
  useEffect(() => onImportFinished((r: ImportFinished) => {
    window.dispatchEvent(new CustomEvent('cp-import-finished', { detail: r }));
    if (r.status === 'REVIEW') {
      toast('✅', translate('bank.importCompleted', { count: r.itemCount }));
    } else if (r.status === 'FAILED') {
      toast('⚠️', translate('bank.importFailed', { error: r.error || translate("bank.unknownError") }));
    } else if (r.status === 'TIMEOUT') {
      toast('⏳', translate('bank.importDelayed'));
    }
  }), [toast]);

  if (!list.length) return null;
  return (
    <div className="import-monitor">
      {list.map((item) => (
        <div
          key={item.id}
          className="import-monitor-item"
          onClick={() => navigate('/bank?review=1')}
          title={t('bank.openReviewFromMonitor')}
        >
          <span className="import-monitor-spin" />
          <div className="import-monitor-txt">
            <b>{t('bank.parsingInterviewCode', { code: item.shortCode })}</b>
            <span>{t('bank.importProgressTime', { note: importProgressNote(item), seconds: (item.elapsedMs / 1000).toFixed(0) })}</span>
          </div>
          <div className="import-monitor-bar">
            <i style={{ width: `${Math.min(100, Math.round((item.step / Math.max(1, item.total)) * 100))}%` }} />
          </div>
        </div>
      ))}
    </div>
  );
}
