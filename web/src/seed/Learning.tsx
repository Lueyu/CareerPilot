import { useEffect, useState } from 'react';
import DailyLearningPlanView, { type Plan } from '../pages/DailyLearningPlanView';
import { useWorkspaceApi } from './api';

const endpoint = '/learning/daily-plan';

export default function Learning() {
  const { request, send } = useWorkspaceApi();
  const [result, setResult] = useState<{ data: Plan } | { error: string }>();

  useEffect(() => {
    const controller = new AbortController();
    void request<Plan>(endpoint, { signal: controller.signal }).then(
      data => { if (!controller.signal.aborted) setResult({ data }); },
      error => {
        if (!controller.signal.aborted) {
          setResult({ error: error instanceof Error ? error.message : '学习计划暂不可用。' });
        }
      },
    );
    return () => controller.abort();
  }, [request]);

  return <DailyLearningPlanView data={result && 'data' in result ? result.data : undefined}
    loading={!result} loadError={result && 'error' in result ? result.error : undefined}
    mode={{ kind: 'registered' }}
    saveSettings={(settings, plan) => send<Plan>(endpoint, 'PUT', { ...settings, target_role: plan.target_role })}
    saveEvidence={(item_key, note, plan) =>
      send<Plan>(endpoint + '/evidence', 'POST', { item_key, note, target_role: plan.target_role })}
    replan={plan => send<Plan>(endpoint + '/reschedule', 'POST', { target_role: plan.target_role })}
  />;
}
