import { Sparkles, Search, ShieldCheck } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';

/**
 * PR-A 底座验证页：
 *   - 单独挂 `data-ui="new"`，跟旧 styles.css 隔离
 *   - 覆盖 Button / Input / Card 的核心 variant，肉眼确认 Indigo / 字体 / 圆角
 *   - 只在开发访问 `?ui=preview` 时挂载，不影响生产 bundle 中的正常业务
 */
export function UiPreview() {
  return (
    <div
      data-ui="new"
      className="min-h-screen bg-background text-foreground p-8 font-sans"
    >
      <div className="mx-auto max-w-4xl space-y-8">
        <header className="space-y-2">
          <h1 className="text-3xl font-semibold tracking-tight">
            UI 底座预览
          </h1>
          <p className="text-muted-foreground">
            Tailwind v4 + shadcn 组件 · 主色 Indigo · Inter / JetBrains Mono
          </p>
        </header>

        <Card>
          <CardHeader>
            <CardTitle>Button 变体</CardTitle>
            <CardDescription>
              default / secondary / outline / ghost / destructive / link · 每个再叠加 sm / default / lg
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-wrap items-center gap-3">
            <Button>
              <Sparkles /> Default
            </Button>
            <Button variant="secondary">Secondary</Button>
            <Button variant="outline">Outline</Button>
            <Button variant="ghost">Ghost</Button>
            <Button variant="destructive">Destructive</Button>
            <Button variant="link">Link</Button>
            <Button size="sm">Small</Button>
            <Button size="lg">Large</Button>
            <Button size="icon" variant="outline" aria-label="search">
              <Search />
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Input</CardTitle>
            <CardDescription>基础输入 · 禁用态 · 占位文字</CardDescription>
          </CardHeader>
          <CardContent className="grid gap-3 md:grid-cols-2">
            <Input placeholder="例如：admin@example.com" />
            <Input placeholder="不可编辑" disabled />
            <Input type="password" placeholder="密码" />
            <Input type="number" placeholder="MR IID" />
          </CardContent>
          <CardFooter className="justify-end gap-2">
            <Button variant="ghost">取消</Button>
            <Button>
              <ShieldCheck /> 提交
            </Button>
          </CardFooter>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>字体样例</CardTitle>
            <CardDescription>正文 Inter · 代码 JetBrains Mono</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <p className="text-base">
              The quick brown fox jumps over the lazy dog. 敏捷的棕色狐狸跳过懒狗。1234567890
            </p>
            <pre className="rounded-md bg-muted p-4 font-mono text-sm">
{`const review = await createReview({
  project_id: 42,
  mr_iid: 128,
});`}
            </pre>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
