import * as React from 'react';
import { ShieldCheck } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';

interface LoginPageProps {
  form: { username: string; password: string };
  onChange: (patch: Partial<{ username: string; password: string }>) => void;
  onSubmit: (event: React.FormEvent<HTMLFormElement>) => void;
  submitting: boolean;
  error: string | null;
  message: string | null;
}

/**
 * 管理台登录页：满屏居中卡片，Linear 风格。
 * label 文本 / 按钮文案与 App.test.tsx 的 getByLabelText / getByRole 断言对齐。
 * 登录使用管理员账号 + 密码（后端签发 Bearer Token），故保留双字段而非单 token 输入。
 */
export function LoginPage({ form, onChange, onSubmit, submitting, error, message }: LoginPageProps) {
  const apiBaseUrl = typeof window !== 'undefined' ? window.location.origin : '';

  return (
    <div className="flex min-h-screen items-center justify-center bg-[#FAFAFA] px-4 font-sans">
      <div className="w-full max-w-sm rounded-lg border border-[#E4E4E7] bg-white p-8">
        <div className="flex flex-col items-center text-center">
          <div className="flex size-6 items-center justify-center rounded-md bg-linear-to-br from-indigo-500 to-indigo-700">
            <ShieldCheck size={14} strokeWidth={2.5} className="text-white" />
          </div>
          <div className="mt-3 text-[13px] font-semibold text-zinc-900">AI Code Reviewer</div>
          <h1 className="mt-1 text-[18px] font-semibold text-zinc-900">管理台登录</h1>
          <p className="mt-1 text-[13px] text-zinc-500">登录后管理 API 将携带 Bearer Token</p>
        </div>

        <form onSubmit={onSubmit} className="mt-6 space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="login-username">管理员账号</Label>
            <Input
              id="login-username"
              value={form.username}
              onChange={(event) => onChange({ username: event.target.value })}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="login-password">管理员密码</Label>
            <Input
              id="login-password"
              type="password"
              value={form.password}
              onChange={(event) => onChange({ password: event.target.value })}
            />
          </div>
          {error ? (
            <div
              role="alert"
              className="rounded-md border border-destructive/50 bg-destructive/10 px-3 py-2 text-[13px] text-destructive"
            >
              {error}
            </div>
          ) : null}
          {message ? (
            <div className="rounded-md border border-primary/30 bg-primary/5 px-3 py-2 text-[13px] text-primary">
              {message}
            </div>
          ) : null}
          <Button type="submit" disabled={submitting} className="w-full">
            {submitting ? '登录中…' : '登录'}
          </Button>
        </form>

        <p className="mt-4 text-center text-[11px] text-zinc-400">API · {apiBaseUrl}</p>
      </div>
    </div>
  );
}
