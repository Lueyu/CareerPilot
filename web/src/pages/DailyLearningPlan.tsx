import { api, useApi, type SettingsOverview } from '../api/client';
import { DemoBanner } from '../components/ui';
import DailyLearningPlanView, { type Plan } from './DailyLearningPlanView';

const endpoint = '/learning/daily-plan';

export default function DailyLearningPlan() {
  const { data, loading, error } = useApi<Plan>(endpoint);
  const { data: settings } = useApi<SettingsOverview>('/settings/overview');
  const goal = [settings?.workspace?.target_role, settings?.workspace?.target_level].filter(Boolean).join(' · ')
    || '待设置目标岗位';

  return <DailyLearningPlanView data={data} loading={loading} loadError={error}
    mode={{ kind: 'personal', goal, banner: <DemoBanner /> }}
    saveSettings={settings => api.put<Plan>(endpoint, settings)}
    saveEvidence={(item_key, note) => api.post<Plan>(endpoint + '/evidence', { item_key, note })}
  />;
}
