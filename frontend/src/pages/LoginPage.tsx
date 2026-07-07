import * as React from 'react';
import { ShieldCheck } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';

interface LoginPageProps {
  form: { username: string; password: string };
  onChange: (patch: Partial<{ username: string; password: string }>) => void;
  onSubmit: (event: React.FormEvent<HTMLFormElement>) => void;
  submitting: boolean;
  error: string | null;
  message: string | null;
}

/**
 * 管理台登录页：满屏居中卡片，挂 `data-ui="new"` 启用新 tokens。
 * label 文本 / 按钮文案与 App.test.tsx 的 getByLabelText / getByRole 断言对齐。
 */
export function LoginPage({ form, onChange, onSubmit, submitting, error, message }: LoginPageProps) {
  return (
    <div
      data-ui="new"
      className="min-h-screen bg-background flex items-center justify-center px-4 font-sans"
    >
      <Card className="w-full max-w-md">
        <CardHeader className="items-center text-center">
          <ShieldCheck className="size-8 text-primary" />
          <div className="text-sm font-medium text-muted-foreground">AI Code Reviewer</div>
          <CardTitle>管理台登录</CardTitle>
          <CardDescription>登录成功后，管理 API 会统一携带 Authorization Bearer Token。</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={onSubmit} className="space-y-4">
            <div className="space-y-2">
              <label htmlFor="login-username" className="text-sm font-medium">管理员账号</label>
              <Input
                id="login-username"
                value={form.username}
                onChange={(event) => onChange({ username: event.target.value })}
              />
            </div>
            <div className="space-y-2">
              <label htmlFor="login-password" className="text-sm font-medium">管理员密码</label>
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
                className="rounded-md border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive"
              >
                {error}
              </div>
            ) : null}
            {message ? (
              <div className="rounded-md border border-primary/30 bg-primary/5 px-3 py-2 text-sm text-primary">
                {message}
              </div>
            ) : null}
            <Button type="submit" disabled={submitting} className="w-full">
              {submitting ? '登录中…' : '登录'}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
