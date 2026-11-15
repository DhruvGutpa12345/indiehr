// IndieHR P1-T11c1: the slug sets the rules read. ihrSlugs.json is a copy of
// plugins/ihr_platform/ihr_roles/frontend_slugs.json (written by `manage.py ihr_frontend_slugs
// --write` from the backend's registry); scripts/check-ihr-frontend.py fails CI when they differ.
import type { IhrSlugSets } from "@/Utils/ihr/access";
import slugs from "@/Utils/ihr/ihrSlugs.json";

export const IHR_SLUG_SETS: IhrSlugSets = slugs;
