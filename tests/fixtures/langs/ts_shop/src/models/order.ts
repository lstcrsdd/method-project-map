export interface Order {
  id: string;
  lines: number[];
}

export class BaseService {
  describe(): string {
    return "base";
  }
}
