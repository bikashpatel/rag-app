import hero800 from "../../assets/brand/hero-800.webp";
import hero1600 from "../../assets/brand/hero-1600.webp";

const ALT =
  "AskNuvexusDoc.com: a friendly robot gathers your PDF, Word, Excel and PowerPoint files and answers a question " +
  "with page references";

/**
 * The owner's brand picture (16:9, with the tagline in it), framed like a mounted print: the large file on the aside,
 * the small one on phones.
 */
export default function HeroArt({ phone = false }: { phone?: boolean }) {
  return (
    <div className="login-art">
      {phone ? (
        <img src={hero800} width={800} height={450} alt={ALT} decoding="async" />
      ) : (
        <img
          src={hero1600}
          srcSet={`${hero800} 800w, ${hero1600} 1600w`}
          sizes="(min-width: 1280px) 560px, calc(52vw - 80px)"
          width={1600}
          height={900}
          alt={ALT}
          decoding="async"
          fetchPriority="high"
        />
      )}
    </div>
  );
}
