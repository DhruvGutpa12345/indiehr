import careConfig from "@careConfig";
import { useEffect } from "react";

export interface IPageTitleProps {
  title: string;
}

export default function PageHeadTitle({ title }: IPageTitleProps) {
  useEffect(() => {
    const prevTitle = document.title;
    if (title) {
      document.title = `${title} | ${careConfig.titleSuffix}`;
    } else {
      document.title = careConfig.titleSuffix;
    }
    return () => {
      document.title = prevTitle;
    };
  }, [title]);

  return <></>;
}
