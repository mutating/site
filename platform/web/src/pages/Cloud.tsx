import { useI18n } from "../lib/i18n";
import { Empty, Heading } from "../components/UI";
export function Cloud() {
  const { t } = useI18n();
  return (
    <>
      <Heading index={`03 / ${t("cloudTag")}`} title={t("cloud")} />
      <section className="cloud-empty panel">
        <span className="badge">{t("soon")}</span>
        <Empty icon="cloud" title={t("cloudTitle")}>
          {t("cloudLead")}
        </Empty>
      </section>
    </>
  );
}
