const bankUrl = new URL('../data/bank.json', import.meta.url).href;

export async function loadLegacyBank(): Promise<{ questions: { id: string; html?: string }[] }> {
  const response = await fetch(bankUrl);
  if (!response.ok) throw new Error('Legacy bank unavailable');
  return response.json();
}
