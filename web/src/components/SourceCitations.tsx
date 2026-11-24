import { useT } from '../i18n';
import React from 'react';
import {SourceCitation,SourceExcerpt} from '../lib/projectIntelligence';

export default function SourceCitations({citations,sourceExcerpts=[]}: {citations:SourceCitation[];sourceExcerpts?:SourceExcerpt[]}) {
  const t = useT();
  return <ul className="hint" style={{paddingLeft:20}}>{citations.map((c,i)=>{
    const source=sourceExcerpts.find(s=>s.path===c.path && s.start_line===c.start_line && s.end_line===c.end_line && (!c.source_id || s.source_id===c.source_id) && (!c.source_version || s.source_version===c.source_version));
    return <li key={`${c.path}-${c.start_line}-${i}`}><code>{c.path}:{c.start_line}{c.end_line!==c.start_line?`–${c.end_line}`:''}</code>
      {source && <details style={{margin:'6px 0 12px'}}><summary>{t('shell.showExcerpt')}</summary><p>{t('shell.excerptProvenance')}</p><pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere',maxHeight:380,overflow:'auto',background:'var(--bg)',padding:12,borderRadius:6}}><code>{source.excerpt}</code></pre>{source.excerpt_truncated && <p>{t('shell.excerptTruncated')}</p>}</details>}
    </li>;
  })}</ul>;
}
