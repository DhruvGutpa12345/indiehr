import careConfig from "@careConfig";
import { Link } from "raviger";
import { useTranslation } from "react-i18next";

export const AuthHero = () => {
  const { urls, stateLogo, customLogo, customLogoAlt, branding } = careConfig;
  const customDescriptionHtml = __CUSTOM_DESCRIPTION_HTML__;
  const { t } = useTranslation();

  const logos = [stateLogo, customLogo].filter(
    (logo) => logo?.light || logo?.dark,
  );

  return (
    <div
      // IndieHR: the hospital's brand colour.
      style={
        branding && {
          background: `linear-gradient(to bottom right, ${branding.primary_colour}, #111827)`,
        }
      }
      className="login-hero order-last relative flex flex-auto flex-col justify-between p-6 md:order-first md:h-full md:w-[calc(50%+130px)] md:flex-none md:p-0 md:px-16 md:pr-[calc(4rem+130px)]"
    >
      <div></div>
      <div className="mt-4 flex flex-col items-start rounded-lg py-4 md:mt-12">
        <div className="mb-4 hidden items-center gap-6 md:flex">
          {logos.map((logo, index) =>
            logo && logo.light ? (
              <div key={index} className="flex items-center">
                <img
                  src={logo.light}
                  className="h-16 rounded-lg py-3"
                  alt="state logo"
                />
              </div>
            ) : null,
          )}
          {logos.length === 0 && (
            <a
              href={urls.ohcn}
              className="inline-block"
              target="_blank"
              rel="noopener noreferrer"
            >
              <img
                src={customLogoAlt?.light ?? "/images/ohc_logo_light.svg"}
                className="h-8"
                alt="Open Healthcare Network logo"
              />
            </a>
          )}
        </div>
        <div className="max-w-lg">
          <h1 className="text-4xl font-black leading-tight tracking-wider text-white lg:text-5xl">
            {branding?.name ?? t("care")}
          </h1>
          {customDescriptionHtml ? (
            <div className="py-6">
              <div
                className="max-w-xl text-secondary-400"
                dangerouslySetInnerHTML={{
                  __html: __CUSTOM_DESCRIPTION_HTML__,
                }}
              />
            </div>
          ) : (
            <div className="max-w-xl py-6 pl-1 text-base font-semibold text-secondary-400 md:text-lg lg:text-xl">
              {t("goal")}
            </div>
          )}
        </div>
      </div>
      <div className="mb-6 flex items-center">
        <div className="max-w-lg text-xs md:text-sm">
          {/* IndieHR: our platform credit; licences stay one click away. */}
          <div className="mb-2 ml-1 text-secondary-400">
            {t("powered_by")} {branding?.platform ?? "IndieHR"}
          </div>
          <div className="mx-auto mt-2">
            <Link
              href="/licenses"
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary-400 hover:text-primary-500"
            >
              {t("third_party_software_licenses")}
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
};
