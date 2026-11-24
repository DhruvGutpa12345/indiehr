import { Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { SectionTitle } from "@/components/ui/typography";

import { userChildProps } from "@/components/Common/UserColumns";
import UserDeleteDialog from "@/components/Users/UserDeleteDialog";
import { UserProfileSection } from "@/components/Users/UserProfileSection";
import useAuthUser from "@/hooks/useAuthUser";

export default function UserSecurityTab(props: userChildProps) {
  const { t } = useTranslation();
  const authUser = useAuthUser();
  const isOwnAccount = authUser.username === props.userData.username;

  return (
    <div className="max-w-3xl space-y-8 py-6">
      <div className="space-y-1.5">
        <SectionTitle>{t("USERMANAGEMENT_TAB__SECURITY")}</SectionTitle>
      </div>
      {/* IndieHR (P1-T4b, ADR-009): passwords and two-factor live in Keycloak, so CARE's
          "Update password" and "Two-factor authentication" sections are not rendered. */}
      {authUser.is_superuser && (
        <UserProfileSection
          title={t("delete_account")}
          description={t("delete_account_note")}
          icon={Trash2}
        >
          <UserDeleteDialog
            user={props.userData}
            isOwnAccount={isOwnAccount}
            trigger={
              <Button variant="destructive" size="sm">
                <Trash2 aria-hidden="true" />
                {t("delete_account")}
              </Button>
            }
          />
        </UserProfileSection>
      )}
    </div>
  );
}
