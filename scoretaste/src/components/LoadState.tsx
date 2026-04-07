import type { ReactNode } from "react";
import { t } from "../i18n";

type Props = { children: ReactNode };

export function PageMain({ children }: Props) {
  return <main className="visitor-page-main">{children}</main>;
}

export function LoadingBlock() {
  return (
    <PageMain>
      <p>{t("common.loading")}</p>
    </PageMain>
  );
}

export function ErrorBlock({ title, hint }: { title: string; hint?: string }) {
  return (
    <PageMain>
      <p role="alert">
        <strong>{title}</strong>
      </p>
      {hint ? <p>{hint}</p> : null}
    </PageMain>
  );
}
