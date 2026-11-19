import React, {useEffect, useRef, useState} from 'react';
import {useNavigate} from 'react-router-dom';
import {api} from '../api/client';
import {Modal} from './ui';
import type {AutopilotSettings, AutopilotSummary} from '../lib/projectIntelligence';
import {messageOf} from '../lib/questionWorkshop';

interface Props {
  projectId: number;
  name: string;
  analysis?: AutopilotSummary;
  repository?:string;
  branch?:string;
  onQueued?: () => void;
}
export default function ProjectScanButton({projectId, name, analysis, onQueued, repository, branch}: Props) {
  const navigate = useNavigate();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [settings, setSettings] = useState<AutopilotSettings>();
  useEffect(() => {
    if (!confirming) return;
    let active = true;
    api.get<AutopilotSettings>('/project-autopilot/settings').then(value => {
      if (active) setSettings(value);
    }).catch(() => { if (active) setSettings(undefined); });
    return () => { active = false; };
  }, [confirming]);
  const requestKey = useRef('');
  const sending = useRef(false);
  const processUrl = '/assets/project/' + projectId + '?tab=process';
  const active = ['WAITING_WORKER', 'QUEUED', 'RUNNING', 'RETRY_WAIT'].includes(analysis?.latest_run?.status ?? '');
  const consented = analysis?.enabled && !analysis.consent_required;

  async function start() {
    if (sending.current) return;
    sending.current = true;
    setBusy(true);
    setError('');
    try {
      requestKey.current ||= crypto.randomUUID();
      await api.post('/projects/' + projectId + '/ai-scan', {request_key: requestKey.current, acknowledge_source_transfer: !consented});
      requestKey.current = '';
      setConfirming(false);
      onQueued?.();
      navigate(processUrl);
    } catch (e) { setError(messageOf(e)); }
    finally { sending.current = false; setBusy(false); }
  }
  return <>
    <button type="button" className="btn btn-primary project-scan-button" disabled={busy} onClick={() => {
      if (active) navigate(processUrl);
      else if (consented) void start();
      else { setError(''); setConfirming(true); }
    }}>{active ? '查看扫描进度' : busy ? '正在提交…' : 'AI 扫描'}</button>
    {error && !confirming && <span role="alert">{error}</span>}
    {confirming && <Modal title={'扫描「' + name + '」'} onClose={() => {if (!busy) setConfirming(false);}} footer={<>
      <button className="btn btn-outline" disabled={busy} onClick={() => setConfirming(false)}>取消</button>
      <button className="btn btn-primary" disabled={busy} onClick={() => void start()}>{busy ? '正在提交…' : '确认并扫描此项目'}</button>
    </>}>
      <dl className="scan-scope">
        <div><dt>仓库与分支</dt><dd>{repository || name} / {branch || '默认分支'}</dd></div>
        <div><dt>读取范围</dt><dd>当前仓库可分析的源码</dd></div>
        <div><dt>模型路由</dt><dd>{settings?.provider_display_name || 'Auto · 学习聊天模型配置'}</dd></div>
        <div><dt>整次扫描预算</dt><dd>{settings?.budget ? [settings.budget.max_model_calls ? settings.budget.max_model_calls+' 次调用' : '调用次数不限', settings.budget.max_source_bytes ? settings.budget.max_source_bytes+' 字节' : '读取总量不限'].join(' · ') : '沿用已保存的设置，默认不限'}</dd></div>
      </dl>
      <p>Auto 会将这个仓库的相关源码片段发送给已配置的模型服务，按扫描阶段选择模型。任务在后台运行，可在分析过程中暂停。</p>
      <p className="hint">只授权此次扫描。</p>
      {error && <p role="alert">{error}</p>}
    </Modal>}
  </>;
}
