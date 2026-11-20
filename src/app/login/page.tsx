"use client";

import { LoginPage } from "@/components/login-page";

// 登录页挂载点（(app) 之外=整页渲染）。登录成功后由 LoginPage 自身
// window.location.href = consumeLoginNext() 整页回跳（强制刷新重建全部 WebSocket）。
export default function Login() {
  return <LoginPage />;
}
