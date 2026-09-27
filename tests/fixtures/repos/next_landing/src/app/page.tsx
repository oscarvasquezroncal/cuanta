import { CartDrawer } from "../components/cart-drawer";
import { HeroNutFall3D } from "../components/hero-nutfall-3d/view";
import { ProductGrid } from "../components/product-grid";
import { Reveal } from "../components/reveal";

export default function LandingPage() {
  return <main><HeroNutFall3D /><Reveal /><ProductGrid /><CartDrawer /></main>;
}
