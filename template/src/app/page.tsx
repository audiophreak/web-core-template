import { Button } from "@/components/ui/button";
import appInfo from "@/lib/app-info.json";

export default function Home() {
  return (
    <main className="flex flex-1 flex-col items-center justify-center gap-4 p-8 text-center">
      <h1 className="text-3xl font-semibold tracking-tight">{appInfo.name}</h1>
      <p className="max-w-md text-muted-foreground">{appInfo.description}</p>
      <Button asChild>
        <a href="https://nextjs.org/docs">Next.js documentation</a>
      </Button>
    </main>
  );
}
