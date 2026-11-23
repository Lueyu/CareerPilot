import { api, useApi } from '../api/client';
import { DemoBanner } from '../components/ui';
import DailyLearningPlanView, { type Plan } from './DailyLearningPlanView';

const endpoint = '/learning/daily-plan';

export default function DailyLearningPlan() {
  const { data, loading, error } = useApi<Plan>(endpoint);

  return <DailyLearningPlanView data={data} loading={loading} loadError={error}
    mode={{ kind: 'personal', goal: 'AI Agent 应用开发 · 社招', banner: <DemoBanner /> }}
    saveSettings={settings => api.put<Plan>(endpoint, settings)}
    saveEvidence={(item_key, note) => api.post<Plan>(endpoint + '/evidence', { item_key, note })}
  />;
}
