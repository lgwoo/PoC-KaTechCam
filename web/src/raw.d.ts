// Vite 의 ?raw 임포트. 파이프라인 페이지가 server/ 의 파이썬 소스를 그대로 읽어
// 코드 조각을 잘라 쓴다 — vite-env.d.ts 의 기본 선언은 .py 를 모른다.
declare module "*.py?raw" {
  const content: string;
  export default content;
}
