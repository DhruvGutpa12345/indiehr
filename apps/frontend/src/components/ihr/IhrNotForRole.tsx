// IndieHR P1-T11c1 (design-c.md §2.3): a typed or old address for a page that is not part of
// the user's role. Makes no API call of its own.
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";

import Page from "@/components/Common/Page";

import { HrefLink } from "@/lib/router/HrefLink";

interface IhrNotForRoleProps {
  facilityId?: string;
}

export function IhrNotForRole({ facilityId }: IhrNotForRoleProps) {
  const { t } = useTranslation();
  return (
    <Page title={t("ihr_not_for_role")} hideTitleOnPage>
      <div
        className="mx-auto mt-10 max-w-lg text-center"
        data-ihr="not-for-role"
      >
        <h1 className="text-2xl font-semibold text-gray-900">
          {t("ihr_not_for_role")}
        </h1>
        <p className="mt-2 text-gray-600">{t("ihr_not_for_role_help")}</p>
        {facilityId && (
          <Button asChild className="mt-6" variant="outline">
            <HrefLink href={`/facility/${facilityId}/overview`}>
              {t("ihr_go_to_overview")}
            </HrefLink>
          </Button>
        )}
      </div>
    </Page>
  );
}
