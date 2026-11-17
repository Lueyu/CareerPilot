import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {StaticRouter} from 'react-router-dom/server.js';
import Workshop from '../src/pages/QuestionWorkshop.tsx';

test('workshop starts with real loading state and no invented projects or question results',()=>{
  const html=renderToStaticMarkup(React.createElement(StaticRouter,{location:'/bank/workshop'},React.createElement(Workshop)));
  assert.match(html,/面经与补题工作台/);
  assert.doesNotMatch(html,/DEMO|所有能力达到|Q-018|AIDR|Squabble/);
  assert.match(html,/加载/);
});
