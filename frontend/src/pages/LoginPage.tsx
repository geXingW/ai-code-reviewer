/**
 * 管理台登录页（antd 版）：满屏居中卡片。
 * 组件自管表单与提交（调 loginAdmin 落 sessionStorage），登录成功回调 onSuccess。
 */

import { useState } from 'react';
import { SafetyOutlined } from '@ant-design/icons';
import { Alert, Button, Card, Form, Input } from 'antd';

import { loginAdmin } from '../api';

interface LoginPageProps {
  onSuccess: () => void;
}

export function LoginPage({ onSuccess }: LoginPageProps) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const apiBaseUrl = typeof window !== 'undefined' ? window.location.origin : '';

  async function handleSubmit(values: { username: string; password: string }) {
    setSubmitting(true);
    setError(null);
    try {
      const username = values.username.trim();
      const password = values.password;
      if (!username || !password) {
        throw new Error('管理员账号和密码不能为空。');
      }
      await loginAdmin(username, password);
      onSuccess();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '登录失败');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-[#FAFAFA] px-4 font-sans">
      <Card style={{ width: '100%', maxWidth: 384 }}>
        <div className="flex flex-col items-center text-center">
          <div className="flex size-8 items-center justify-center rounded-md bg-linear-to-br from-indigo-500 to-indigo-700">
            <SafetyOutlined style={{ color: '#fff', fontSize: 16 }} />
          </div>
          <div className="mt-3 text-[13px] font-semibold text-zinc-900">AI Code Reviewer</div>
          <h1 className="mt-1 text-[18px] font-semibold text-zinc-900">管理台登录</h1>
          <p className="mt-1 text-[13px] text-zinc-500">请输入用户名和密码登录管理台</p>
        </div>

        <Form
          layout="vertical"
          className="mt-6"
          onFinish={(values) => void handleSubmit(values)}
        >
          <Form.Item
            label="用户名"
            name="username"
            rules={[{ required: true, message: '请输入用户名' }]}
          >
            <Input autoComplete="username" />
          </Form.Item>
          <Form.Item
            label="密码"
            name="password"
            rules={[{ required: true, message: '请输入密码' }]}
          >
            <Input.Password autoComplete="current-password" />
          </Form.Item>

          {error ? <Alert type="error" showIcon message={error} className="mb-4" /> : null}

          <Button type="primary" htmlType="submit" block loading={submitting}>
            登录
          </Button>
        </Form>

        <p className="mt-4 text-center text-[11px] text-zinc-400">API · {apiBaseUrl}</p>
      </Card>
    </div>
  );
}
