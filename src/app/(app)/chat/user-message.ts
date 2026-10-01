// 系统生成的用户角色行 → 前端用户气泡消息（wakeup_relay user_message推送的处理面）
//
// 角色保真（dev-reports 21:40轮遗留#4销账）：唤醒触发行是role='user'落库行，
// 历史加载（chat-client loadHistory：role==='user'→用户气泡）显示为用户气泡；
// 此前live relay以agent_message透出→live显示agent气泡、刷新后翻转为用户气泡。
// 本模块是session/update user_message分支的消息构造单一真源（composeAskAnswers
// 同款纯函数惯例：可独立编译单测，chat-client只做接线）。
//
// 形状与loadHistory映射产物一致：{ id, role:'user', parts:[{type:'text',text}], timestamp }。

export interface UserMessageLike {
  id: string;
  role: 'user';
  parts: Array<{ type: 'text'; text: string }>;
  timestamp: Date;
}

export function buildUserMessage(text: string, id?: string): UserMessageLike {
  return {
    id: id || Date.now().toString(),
    role: 'user',
    parts: [{ type: 'text', text }],
    timestamp: new Date(),
  };
}
