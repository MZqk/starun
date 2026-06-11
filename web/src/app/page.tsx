import Link from "next/link";

export default function HomePage() {
  return (
    <main>
      <h1>Starun</h1>
      <p>面向有经验天文摄影爱好者的后期分析与自动出图平台。</p>
      <nav aria-label="产品功能">
        <Link href="/analysis">开始分析</Link>
        <Link href="/processing">自动出图</Link>
      </nav>
    </main>
  );
}
