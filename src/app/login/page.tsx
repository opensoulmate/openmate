"use client";

import { useRouter } from "next/navigation";
import { LoginPage } from "@/components/login-page";
import { consumeLoginNext } from "@/lib/auth-errors";

export default function Login() {
  const router = useRouter();
  // 登录成功回跳原路径（401 接管时已存；无则落 /chat，与既有语义一致）
  return <LoginPage onLogin={() => router.push(consumeLoginNext())} />;
}
