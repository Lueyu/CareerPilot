/** Presentation and edit affordances follow the persisted source, not company text. */
export function bankSourcePresentation(question:{source_type:string;company?:string|null}) {
  const isPrivate=question.source_type==='USER_PRIVATE' || question.source_type==='GENERATED';
  return {
    company:question.company?.trim() || (isPrivate?'个人题库':'公开题库'),
    isPrivate,
    editable:isPrivate,
    label:question.source_type==='GENERATED'?'模型生成':isPrivate?'个人导入':'共享题库 · 只读',
  };
}
